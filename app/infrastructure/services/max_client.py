import mimetypes
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception,
)

from app.config import settings
from app.domain.interfaces.max_client import MaxAPIClient

MAX_MESSAGE_TEXT_LEN = 4000


def _clip_message_text(text: str, limit: int = MAX_MESSAGE_TEXT_LEN) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _get_verify_path() -> str | bool:
    cert_path = Path(__file__).parent.parent.parent.parent / "certs" / "russian_trusted_root_ca.pem"
    if cert_path.exists():
        return str(cert_path)
    return True


def _is_attachment_not_ready(response: httpx.Response) -> bool:
    """MAX returns 400 while uploaded audio/video is still processing."""
    body = (response.text or "").lower()
    return (
        "attachment.not.ready" in body
        or "not.processed" in body
        or "attachment.video.not.processed" in body
    )


def _is_retryable_max_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 408 or status == 429 or status >= 500:
            return True
        # Upload token can be returned before MAX finishes processing media.
        if status == 400 and _is_attachment_not_ready(exc.response):
            return True
    return False


class MaxAPIHTTPClient(MaxAPIClient):
    def __init__(self, token: str | None = None) -> None:
        self._token = token or settings.max_api.token
        self._base_url = settings.max_api.base_url
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": self._token},
            timeout=httpx.Timeout(60.0, connect=10.0),
            verify=_get_verify_path(),
        )

    async def close(self) -> None:
        await self._client.aclose()

    # Large audio (~15–20 MB fairy tales) can take several minutes for MAX to process.
    # Keep retrying attachment.not.ready long enough before giving up.
    @retry(
        stop=stop_after_attempt(20),
        wait=wait_exponential(multiplier=2, min=3, max=60),
        retry=retry_if_exception(_is_retryable_max_error),
        reraise=True,
    )
    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        logger.debug(f"MAX API {method} {path} params={params}")
        response = await self._client.request(
            method=method, url=path, params=params, json=json
        )
        if response.status_code >= 400:
            logger.error(
                f"MAX API {method} {path} status={response.status_code} "
                f"body={response.text[:500]}"
            )
        response.raise_for_status()
        return response.json()

    async def send_message(
        self,
        chat_id: int,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
        fmt: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"text": _clip_message_text(text)}
        if attachments:
            body["attachments"] = attachments
        if fmt:
            body["format"] = fmt
        params: dict[str, Any] = {}
        if chat_id:
            params["chat_id"] = chat_id
        return await self._request("POST", "/messages", params=params, json=body)

    async def send_message_to_user(
        self, user_id: int, text: str,
        attachments: list[dict[str, Any]] | None = None,
        fmt: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"text": _clip_message_text(text)}
        if attachments:
            body["attachments"] = attachments
        if fmt:
            body["format"] = fmt
        return await self._request("POST", "/messages", params={"user_id": user_id}, json=body)

    async def get_messages(self, chat_id: int, count: int = 50) -> list[dict[str, Any]]:
        result = await self._request(
            "GET", "/messages", params={"chat_id": chat_id, "count": min(count, 100)}
        )
        return result.get("messages", [])

    async def get_message_images(self, chat_id: int, count: int = 10) -> list[str]:
        messages = await self.get_messages(chat_id, count)
        image_urls: list[str] = []
        for msg in messages:
            attachments = msg.get("body", {}).get("attachments", [])
            for att in attachments if attachments else []:
                if att.get("type") == "image":
                    url = att.get("payload", {}).get("url", "")
                    if url:
                        image_urls.append(url)
        return image_urls

    async def get_chat(self, chat_id: int) -> dict[str, Any]:
        return await self._request("GET", f"/chats/{chat_id}")

    async def patch_chat(
        self,
        chat_id: int,
        title: str | None = None,
        icon: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if icon is not None:
            body["icon"] = icon
        return await self._request("PATCH", f"/chats/{chat_id}", json=body)

    async def upload_file(self, file_path: str, file_type: str) -> str:
        upload_response = await self._request(
            "POST", "/uploads", params={"type": file_type}
        )
        upload_url = upload_response["url"]

        file_name = Path(file_path).name
        mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as upload_client:
            with open(file_path, "rb") as f:
                upload_result = await upload_client.post(
                    upload_url,
                    files={"data": (file_name, f, mime_type)},
                )
            upload_result.raise_for_status()

        try:
            result = upload_result.json()
            if "photos" in result:
                for photo_data in result["photos"].values():
                    return photo_data["token"]
            return result.get("token", "")
        except Exception:
            logger.warning(f"Upload response not JSON, body={upload_result.text[:200]}")
            return upload_response.get("token", "")

    async def get_me(self) -> dict[str, Any]:
        return await self._request("GET", "/me")

    async def get_chat_members_me(self, chat_id: int) -> dict[str, Any]:
        return await self._request("GET", f"/chats/{chat_id}/members/me")

    async def answer_callback(
        self, callback_id: str, text: str | None = None, show_alert: bool = False
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/answers",
            params={"callback_id": callback_id},
            json={"text": text, "show_alert": show_alert},
        )

    async def edit_message(
        self, message_id: int, text: str, attachments: list[dict[str, Any]] | None = None,
        fmt: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"text": text}
        if attachments:
            body["attachments"] = attachments
        if fmt:
            body["format"] = fmt
        return await self._request(
            "PUT", "/messages", params={"message_id": message_id}, json=body
        )

    async def setup_webhook(
        self, url: str, secret: str, update_types: list[str] | None = None
    ) -> dict[str, Any]:
        types = update_types or [
            "bot_added", "bot_started", "bot_stopped", "bot_removed",
            "message_created", "message_callback", "message_edited", "message_removed",
            "user_added", "user_removed",
        ]
        body: dict[str, Any] = {"url": url, "update_types": types}
        if secret:
            body["secret"] = secret
        return await self._request("POST", "/subscriptions", json=body)

    async def delete_webhook(self) -> dict[str, Any]:
        return await self._request("DELETE", "/subscriptions")
