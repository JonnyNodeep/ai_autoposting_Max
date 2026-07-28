from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from loguru import logger

from app.config import settings
from app.infrastructure.services import vidgo_tasks


class VidGoClient:
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or settings.vidgo.api_key
        self._base_url = "https://api.vidgo.ai"
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=httpx.Timeout(120.0, connect=30.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _build_callback_url() -> str | None:
        base = (settings.vidgo.callback_url or "").strip()
        if not base:
            return None
        token = (settings.vidgo.webhook_token or "").strip()
        if not token:
            return base
        parsed = urlparse(base)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["token"] = token
        return urlunparse(parsed._replace(query=urlencode(query)))

    async def upload_image(self, file_path: str) -> str:
        import mimetypes
        from pathlib import Path

        mime_type = mimetypes.guess_type(file_path)[0] or "image/png"
        file_name = Path(file_path).name

        with open(file_path, "rb") as f:
            response = await self._client.post(
                "/api/common/upload/stream",
                files={"file": (file_name, f, mime_type)},
            )
        response.raise_for_status()
        data = response.json()
        file_url = data["data"]["file_url"]
        logger.info(f"VidGo upload: {file_path} -> {file_url}")
        return file_url

    async def submit_video(
        self,
        model: str,
        prompt: str,
        image_url: str,
        duration: int = 6,
        mode: str = "normal",
        resolution: str = "720p",
        task_meta: dict[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "input": {
                "prompt": prompt,
                "image_urls": [image_url],
            },
        }

        callback_url = self._build_callback_url()
        if callback_url:
            payload["callback_url"] = callback_url

        if model == "grok-imagine":
            payload["input"]["duration"] = duration
            payload["input"]["mode"] = mode
        elif model == "wan2.5-image-to-video":
            payload["input"]["duration"] = duration
            payload["input"]["resolution"] = resolution

        logger.info(f"VidGo submit: model={model} prompt_len={len(prompt)}")
        response = await self._client.post("/api/generate/submit", json=payload)
        response.raise_for_status()
        data = response.json()
        task_id = data["data"]["task_id"]
        logger.info(f"VidGo task created: {task_id}")

        if task_meta is not None:
            await vidgo_tasks.register_task(task_id, task_meta)

        return task_id

    async def get_task_status(self, task_id: str) -> dict[str, Any]:
        response = await self._client.get(f"/api/generate/status/{task_id}")
        response.raise_for_status()
        return response.json()["data"]

    async def wait_for_task(
        self,
        task_id: str,
        poll_interval: int = 5,
        timeout: int = 900,
        on_progress: Any | None = None,
    ) -> dict[str, Any]:
        """Wait for webhook result in Redis, falling back to status polling."""
        deadline = datetime.now(UTC).timestamp() + timeout
        started = datetime.now(UTC).timestamp()
        last_progress_notify = 0

        while True:
            stored = await vidgo_tasks.get_stored_result(task_id)
            if stored:
                status = stored.get("status")
                if status == "finished":
                    return stored
                if status == "failed":
                    raise RuntimeError(
                        f"Video generation failed: {stored.get('error_message', 'unknown')}"
                    )

            task = await self.get_task_status(task_id)
            status = task["status"]
            progress = task.get("progress", 0)
            logger.debug(f"VidGo task {task_id}: status={status} progress={progress}%")

            if status == "finished":
                await vidgo_tasks.store_result(task_id, task)
                return task
            if status == "failed":
                await vidgo_tasks.store_result(task_id, task)
                raise RuntimeError(f"Video generation failed: {task.get('error_message', 'unknown')}")

            now = datetime.now(UTC).timestamp()
            if now > deadline:
                raise TimeoutError(f"Video generation timed out after {timeout}s")

            elapsed = int(now - started)
            if on_progress and elapsed - last_progress_notify >= 60:
                last_progress_notify = elapsed
                await on_progress(elapsed, progress)

            await asyncio.sleep(poll_interval)

    async def poll_task(
        self,
        task_id: str,
        poll_interval: int = 5,
        timeout: int = 600,
    ) -> dict[str, Any]:
        return await self.wait_for_task(task_id, poll_interval=poll_interval, timeout=timeout)
