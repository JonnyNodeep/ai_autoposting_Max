from datetime import datetime, UTC
from typing import Any

import httpx
from loguru import logger

from app.config import settings


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
    ) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "callback_url": settings.vidgo.callback_url or "https://autopost.aigarage.fun/webhook/vidgo",
            "input": {
                "prompt": prompt,
                "image_urls": [image_url],
            },
        }

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
        return task_id

    async def get_task_status(self, task_id: str) -> dict[str, Any]:
        response = await self._client.get(f"/api/generate/status/{task_id}")
        response.raise_for_status()
        return response.json()["data"]

    async def poll_task(
        self,
        task_id: str,
        poll_interval: int = 5,
        timeout: int = 600,
    ) -> dict[str, Any]:
        import asyncio

        deadline = datetime.now(UTC).timestamp() + timeout
        while True:
            task = await self.get_task_status(task_id)
            status = task["status"]
            progress = task.get("progress", 0)
            logger.debug(f"VidGo task {task_id}: status={status} progress={progress}%")

            if status == "finished":
                return task
            if status == "failed":
                raise RuntimeError(f"Video generation failed: {task.get('error_message', 'unknown')}")

            if datetime.now(UTC).timestamp() > deadline:
                raise TimeoutError(f"Video generation timed out after {timeout}s")

            await asyncio.sleep(poll_interval)
