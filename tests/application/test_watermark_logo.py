from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from app.application.channels.watermark_logo import (
    save_watermark_logo,
    sync_logo_from_chat_icon,
)
from app.domain.entities.channel import Channel


class MockChannelRepo:
    def __init__(self, channel: Channel):
        self._channel = channel

    async def update(self, channel: Channel) -> Channel:
        self._channel = channel
        return channel


def _png_bytes(color=(255, 0, 0, 255)) -> bytes:
    from io import BytesIO

    buf = BytesIO()
    Image.new("RGBA", (32, 32), color=color).save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_save_watermark_logo_from_local_file(tmp_path, monkeypatch):
    import app.application.channels.watermark_logo as wm

    monkeypatch.setattr(wm, "UPLOAD_DIR", tmp_path)
    src = tmp_path / "src.png"
    src.write_bytes(_png_bytes())
    before = src.read_bytes()
    ch = Channel(id=7, owner_id=1, max_chat_id=100, title="T")
    repo = MockChannelRepo(ch)

    path = await save_watermark_logo(ch, repo, str(src))

    dest = tmp_path / "logos" / "7.png"
    assert path == str(dest)
    assert dest.exists()
    assert ch.logo_path == str(dest)
    assert src.read_bytes() == before


@pytest.mark.asyncio
async def test_save_watermark_logo_from_url(tmp_path, monkeypatch):
    import app.application.channels.watermark_logo as wm

    monkeypatch.setattr(wm, "UPLOAD_DIR", tmp_path)
    ch = Channel(id=3, owner_id=1, max_chat_id=100, title="T")
    repo = MockChannelRepo(ch)
    payload = _png_bytes((0, 255, 0, 255))

    class _Resp:
        content = payload

        def raise_for_status(self):
            return None

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url):
            assert url == "https://cdn.example.com/icon.png"
            return _Resp()

    monkeypatch.setattr(wm.httpx, "AsyncClient", lambda **kw: _Client())
    path = await save_watermark_logo(ch, repo, "https://cdn.example.com/icon.png")
    assert Path(path).read_bytes()
    assert ch.logo_path == str(tmp_path / "logos" / "3.png")


@pytest.mark.asyncio
async def test_sync_logo_from_chat_icon_missing_url(tmp_path, monkeypatch):
    import app.application.channels.watermark_logo as wm

    monkeypatch.setattr(wm, "UPLOAD_DIR", tmp_path)
    ch = Channel(id=1, owner_id=1, max_chat_id=55, title="T")
    repo = MockChannelRepo(ch)
    max_client = AsyncMock()
    max_client.get_chat.return_value = {"icon": {}}

    path = await sync_logo_from_chat_icon(ch, repo, max_client)
    assert path is None
    assert ch.logo_path is None


@pytest.mark.asyncio
async def test_sync_logo_from_chat_icon_ok(tmp_path, monkeypatch):
    import app.application.channels.watermark_logo as wm

    monkeypatch.setattr(wm, "UPLOAD_DIR", tmp_path)
    ch = Channel(id=2, owner_id=1, max_chat_id=55, title="T")
    repo = MockChannelRepo(ch)
    max_client = AsyncMock()
    max_client.get_chat.return_value = {
        "icon": {"url": "https://i.oneme.ru/icon.png"},
    }
    payload = _png_bytes()

    class _Resp:
        content = payload

        def raise_for_status(self):
            return None

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url):
            return _Resp()

    monkeypatch.setattr(wm.httpx, "AsyncClient", lambda **kw: _Client())
    path = await sync_logo_from_chat_icon(ch, repo, max_client)
    assert path == str(tmp_path / "logos" / "2.png")
    assert Path(path).exists()


def test_extract_image_url_from_message():
    from app.bot.handlers.channels import _extract_image_url_from_message

    msg = {
        "body": {
            "attachments": [
                {"type": "image", "payload": {"url": "https://cdn.example.com/a.png"}},
            ]
        }
    }
    assert _extract_image_url_from_message(msg) == "https://cdn.example.com/a.png"
    assert _extract_image_url_from_message({"body": {"text": "hi"}}) is None
