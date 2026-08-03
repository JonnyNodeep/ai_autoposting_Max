from __future__ import annotations

import re
from html import escape
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from app.config import settings


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def max_markdown_to_telegram_html(text: str) -> str:
    """Convert MAX-style markdown (**bold**, [text](url)) to Telegram HTML.

    Supports nesting like ``**👉 [label](url)**`` used in subscribe CTA.
    """
    placeholders: list[str] = []

    def _hold_link(m: re.Match[str]) -> str:
        label = escape(m.group(1))
        url = escape(m.group(2), quote=True)
        placeholders.append(f'<a href="{url}">{label}</a>')
        return f"\x00L{len(placeholders) - 1}\x00"

    def _hold_bold(m: re.Match[str]) -> str:
        # Inner may already contain link markers (\x00Li\x00); do not escape those away.
        inner = m.group(1)
        parts = re.split(r"(\x00[LB]\d+\x00)", inner)
        rebuilt: list[str] = []
        for part in parts:
            if re.fullmatch(r"\x00[LB]\d+\x00", part or ""):
                rebuilt.append(part)
            else:
                rebuilt.append(escape(part))
        placeholders.append(f"<b>{''.join(rebuilt)}</b>")
        return f"\x00B{len(placeholders) - 1}\x00"

    def _expand(s: str) -> str:
        prev = None
        while prev != s:
            prev = s
            for i, html in enumerate(placeholders):
                s = s.replace(f"\x00L{i}\x00", html).replace(f"\x00B{i}\x00", html)
        return s

    staged = _LINK_RE.sub(_hold_link, text)
    staged = _BOLD_RE.sub(_hold_bold, staged)
    staged = escape(staged)
    # Resolve markers trapped inside other placeholders (nested bold+link).
    for i, html in enumerate(placeholders):
        placeholders[i] = _expand(html)
    return _expand(staged)


class TelegramAPIHTTPClient:
    """Minimal Telegram Bot API client for channel dual-publish."""

    def __init__(self, token: str | None = None) -> None:
        self._token = token if token is not None else settings.telegram.token
        self._base = f"https://api.telegram.org/bot{self._token}"
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))

    @property
    def configured(self) -> bool:
        return bool(self._token)

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def resolve_public_link(chat: dict[str, Any]) -> str | None:
        """Build https://t.me/<username> from getChat result, if public."""
        username = (chat.get("username") or "").strip()
        if not username:
            return None
        return f"https://t.me/{username.lstrip('@')}"

    async def get_chat(self, chat_id: int | str) -> dict[str, Any]:
        payload = await self._post_form("getChat", {"chat_id": chat_id})
        return payload.get("result") or {}

    async def _post_form(self, method: str, data: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(f"{self._base}/{method}", data=data)
        payload = response.json()
        if not payload.get("ok"):
            logger.error(
                f"Telegram API {method} failed: "
                f"{payload.get('error_code')} {payload.get('description')}"
            )
            raise RuntimeError(
                f"Telegram {method}: {payload.get('description') or response.text[:200]}"
            )
        return payload

    async def _post_multipart(
        self,
        method: str,
        data: dict[str, Any],
        files: dict[str, Any],
    ) -> dict[str, Any]:
        response = await self._client.post(
            f"{self._base}/{method}",
            data=data,
            files=files,
        )
        payload = response.json()
        if not payload.get("ok"):
            logger.error(
                f"Telegram API {method} failed: "
                f"{payload.get('error_code')} {payload.get('description')}"
            )
            raise RuntimeError(
                f"Telegram {method}: {payload.get('description') or response.text[:200]}"
            )
        return payload

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        parse_mode: str | None = "HTML",
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:4096],
            "disable_web_page_preview": "true",
        }
        if parse_mode:
            data["parse_mode"] = parse_mode
        return await self._post_form("sendMessage", data)

    async def send_photo(
        self,
        chat_id: int | str,
        photo: str,
        caption: str | None = None,
        parse_mode: str | None = "HTML",
    ) -> dict[str, Any]:
        data: dict[str, Any] = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption[:1024]
        if parse_mode and caption:
            data["parse_mode"] = parse_mode

        if photo.startswith("http://") or photo.startswith("https://"):
            data["photo"] = photo
            return await self._post_form("sendPhoto", data)

        path = Path(photo)
        with path.open("rb") as f:
            return await self._post_multipart(
                "sendPhoto",
                data,
                {"photo": (path.name, f, "image/jpeg")},
            )

    async def send_video(
        self,
        chat_id: int | str,
        video: str,
        caption: str | None = None,
        parse_mode: str | None = "HTML",
    ) -> dict[str, Any]:
        data: dict[str, Any] = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption[:1024]
        if parse_mode and caption:
            data["parse_mode"] = parse_mode

        if video.startswith("http://") or video.startswith("https://"):
            data["video"] = video
            return await self._post_form("sendVideo", data)

        path = Path(video)
        with path.open("rb") as f:
            return await self._post_multipart(
                "sendVideo",
                data,
                {"video": (path.name, f, "video/mp4")},
            )

    async def send_audio(
        self,
        chat_id: int | str,
        audio: str,
        caption: str | None = None,
        parse_mode: str | None = "HTML",
    ) -> dict[str, Any]:
        data: dict[str, Any] = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption[:1024]
        if parse_mode and caption:
            data["parse_mode"] = parse_mode

        if audio.startswith("http://") or audio.startswith("https://"):
            data["audio"] = audio
            return await self._post_form("sendAudio", data)

        path = Path(audio)
        with path.open("rb") as f:
            return await self._post_multipart(
                "sendAudio",
                data,
                {"audio": (path.name, f, "audio/mpeg")},
            )

    async def publish_post(
        self,
        chat_id: int | str,
        text: str,
        *,
        image_url: str | None = None,
        video_path: str | None = None,
        audio_path: str | None = None,
    ) -> dict[str, Any]:
        html = max_markdown_to_telegram_html(text)
        if video_path:
            if len(html) <= 1024:
                return await self.send_video(chat_id, video_path, caption=html)
            await self.send_video(chat_id, video_path)
            return await self.send_message(chat_id, html)
        if audio_path:
            if len(html) <= 1024:
                return await self.send_audio(chat_id, audio_path, caption=html)
            await self.send_audio(chat_id, audio_path)
            return await self.send_message(chat_id, html)
        if image_url:
            if len(html) <= 1024:
                return await self.send_photo(chat_id, image_url, caption=html)
            await self.send_photo(chat_id, image_url)
            return await self.send_message(chat_id, html)
        return await self.send_message(chat_id, html)
