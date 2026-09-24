"""Tests for extended Sunor API client helpers."""

from unittest.mock import AsyncMock

import pytest

from app.infrastructure.services.sunor_client import (
    MusicTrack,
    SunorClientError,
    _is_retryable_create,
    build_continue_input,
    build_custom_music_input,
    build_inspiration_input,
    build_instrumental_input,
    parse_lyrics_text,
    pick_track,
    post_create_task,
)


def test_build_inspiration_input():
    inp = build_inspiration_input(
        gpt_description_prompt="A chill lofi beat",
        make_instrumental=True,
    )
    assert inp["gpt_description_prompt"] == "A chill lofi beat"
    assert inp["make_instrumental"] is True


def test_build_custom_music_input_with_tags():
    inp = build_custom_music_input(
        prompt="[Verse]\nHello",
        tags="pop, upbeat",
        negative_tags="metal",
        title="Song",
        vocal_gender="f",
    )
    assert "[Verse]" in inp["prompt"]
    assert "female vocals" in inp["tags"]
    assert inp["negative_tags"] == "metal"
    assert inp["title"] == "Song"


def test_build_instrumental_input():
    inp = build_instrumental_input(tags="lofi, piano", title="Study")
    assert inp["make_instrumental"] is True
    assert inp["tags"] == "lofi, piano"


def test_build_continue_input():
    inp = build_continue_input(
        continue_clip_id="clip-1",
        continue_at=30,
        prompt="[Bridge]\nMore",
    )
    assert inp["continue_clip_id"] == "clip-1"
    assert inp["continue_at"] == 30
    assert inp["prompt"] == "[Bridge]\nMore"


def test_parse_lyrics_text():
    assert parse_lyrics_text({"text": "Line one"}) == "Line one"
    assert parse_lyrics_text({"result": [{"lyrics": "A B C"}]}) == "A B C"


def test_pick_track_second():
    tracks = [
        MusicTrack(audio_id="1", audio_url="https://a/1.mp3", variant_index=0),
        MusicTrack(audio_id="2", audio_url="https://a/2.mp3", variant_index=1),
    ]
    assert pick_track(tracks, "second").audio_id == "2"


def test_is_retryable_create_transient_and_network():
    assert _is_retryable_create(SunorClientError("x", status_code=502))
    assert _is_retryable_create(SunorClientError("x", status_code=429))
    assert _is_retryable_create(
        SunorClientError("Sunor create network error: boom", status_code=None)
    )
    assert not _is_retryable_create(SunorClientError("x", status_code=400))
    assert not _is_retryable_create(
        SunorClientError("Sunor create response missing task_id", status_code=200)
    )


@pytest.mark.asyncio
async def test_post_create_retries_502_then_succeeds(monkeypatch):
    import app.infrastructure.services.sunor_client as sc

    monkeypatch.setattr(sc.asyncio, "sleep", AsyncMock())

    calls = {"n": 0}

    class FakeResp:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload
            self.headers = {}

        def json(self):
            return self._payload

        @property
        def text(self):
            return str(self._payload)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return FakeResp(502, {"error": "bad gateway"})
            return FakeResp(200, {"data": {"task_id": "task-ok"}})

    monkeypatch.setattr(sc.httpx, "AsyncClient", FakeClient)

    task_id = await post_create_task(
        "https://sunor.example/api/v1",
        "key",
        task_type="music",
        input_data={"prompt": "hi"},
    )
    assert task_id == "task-ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_post_create_no_retry_on_400(monkeypatch):
    import app.infrastructure.services.sunor_client as sc

    monkeypatch.setattr(sc.asyncio, "sleep", AsyncMock())
    calls = {"n": 0}

    class FakeResp:
        status_code = 400
        headers = {}
        text = "bad"

        def json(self):
            return {"error": "bad request"}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            calls["n"] += 1
            return FakeResp()

    monkeypatch.setattr(sc.httpx, "AsyncClient", FakeClient)

    with pytest.raises(SunorClientError) as ei:
        await post_create_task(
            "https://sunor.example/api/v1",
            "key",
            task_type="music",
            input_data={"prompt": "hi"},
        )
    assert ei.value.status_code == 400
    assert calls["n"] == 1
