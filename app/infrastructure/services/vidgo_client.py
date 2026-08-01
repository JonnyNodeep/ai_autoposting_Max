from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from loguru import logger

from app.config import settings
from app.infrastructure.services import vidgo_tasks

DEFAULT_VIDEO_MODEL = "seedance-1.5-pro"
FALLBACK_VIDEO_MODEL = "wan2.5-image-to-video"
_GROK_LEGACY = "grok-imagine"

_SEEDANCE_DEFAULTS: dict[str, Any] = {
    "duration": 4,
    "resolution": "480p",
    "aspect_ratio": "9:16",
    "fixed_lens": False,
    "generate_audio": False,
}
_WAN_I2V_DEFAULTS: dict[str, Any] = {
    "duration": 5,
    "resolution": "720p",
}


def resolve_video_model(model: str | None) -> str:
    """Map legacy grok config to Seedance; default to Seedance."""
    if not model or model == _GROK_LEGACY:
        return DEFAULT_VIDEO_MODEL
    return model


def video_submit_params(model: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build submit_video kwargs for a model from block/config values."""
    cfg = config or {}
    if model == "seedance-1.5-pro":
        return {
            "duration": int(cfg.get("duration", _SEEDANCE_DEFAULTS["duration"])),
            "resolution": str(cfg.get("resolution", _SEEDANCE_DEFAULTS["resolution"])),
            "aspect_ratio": str(cfg.get("aspect_ratio", _SEEDANCE_DEFAULTS["aspect_ratio"])),
            "fixed_lens": bool(cfg.get("fixed_lens", _SEEDANCE_DEFAULTS["fixed_lens"])),
            "generate_audio": bool(cfg.get("generate_audio", _SEEDANCE_DEFAULTS["generate_audio"])),
        }
    if model == "wan2.5-image-to-video":
        return {
            "duration": int(cfg.get("duration", _WAN_I2V_DEFAULTS["duration"])),
            "resolution": str(cfg.get("resolution", _WAN_I2V_DEFAULTS["resolution"])),
        }
    if model == _GROK_LEGACY:
        return {
            "duration": int(cfg.get("duration", 6)),
            "mode": str(cfg.get("mode", "normal")),
        }
    return {
        "duration": int(cfg.get("duration", 6)),
        "resolution": str(cfg.get("resolution", "720p")),
        "mode": str(cfg.get("mode", "normal")),
    }


def build_video_attempts(config: dict[str, Any] | None = None) -> list[tuple[str, dict[str, Any]]]:
    """Primary model (+ Wan I2V fallback unless primary is already Wan)."""
    cfg = dict(config or {})
    primary = resolve_video_model(cfg.get("model"))
    # When remapping grok, force cheap Seedance params regardless of stale duration/mode.
    if (config or {}).get("model") == _GROK_LEGACY:
        primary_cfg = {**_SEEDANCE_DEFAULTS}
    elif primary == "seedance-1.5-pro":
        primary_cfg = {**_SEEDANCE_DEFAULTS, **{k: cfg[k] for k in _SEEDANCE_DEFAULTS if k in cfg}}
    elif primary == "wan2.5-image-to-video":
        primary_cfg = {**_WAN_I2V_DEFAULTS, **{k: cfg[k] for k in _WAN_I2V_DEFAULTS if k in cfg}}
    else:
        primary_cfg = cfg

    attempts: list[tuple[str, dict[str, Any]]] = [(primary, video_submit_params(primary, primary_cfg))]

    fallback = cfg.get("fallback_model") or FALLBACK_VIDEO_MODEL
    if primary != FALLBACK_VIDEO_MODEL and fallback == FALLBACK_VIDEO_MODEL:
        attempts.append((FALLBACK_VIDEO_MODEL, video_submit_params(FALLBACK_VIDEO_MODEL, _WAN_I2V_DEFAULTS)))
    return attempts


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
        aspect_ratio: str = "9:16",
        fixed_lens: bool = False,
        generate_audio: bool = False,
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

        if model == "seedance-1.5-pro":
            payload["input"]["aspect_ratio"] = aspect_ratio
            payload["input"]["resolution"] = resolution
            payload["input"]["duration"] = duration
            payload["input"]["fixed_lens"] = fixed_lens
            payload["input"]["generate_audio"] = generate_audio
        elif model == "wan2.5-image-to-video":
            payload["input"]["duration"] = duration
            payload["input"]["resolution"] = resolution
        elif model == "grok-imagine":
            payload["input"]["duration"] = duration
            payload["input"]["mode"] = mode

        logger.info(f"VidGo submit: model={model} prompt_len={len(prompt)}")
        response = await self._client.post("/api/generate/submit", json=payload)
        response.raise_for_status()
        data = response.json()
        task_id = data["data"]["task_id"]
        logger.info(f"VidGo task created: {task_id}")

        if task_meta is not None:
            await vidgo_tasks.register_task(task_id, task_meta)

        return task_id

    async def generate_video_with_fallback(
        self,
        prompt: str,
        image_url: str,
        config: dict[str, Any] | None = None,
        task_meta: dict[str, Any] | None = None,
        timeout: int = 900,
        on_progress: Any | None = None,
    ) -> dict[str, Any]:
        """Submit + wait, trying primary then Wan I2V fallback on failure."""
        attempts = build_video_attempts(config)
        last_error: Exception | None = None

        for index, (model, params) in enumerate(attempts):
            try:
                logger.info(
                    f"VidGo attempt {index + 1}/{len(attempts)}: model={model} params={params}"
                )
                task_id = await self.submit_video(
                    model=model,
                    prompt=prompt,
                    image_url=image_url,
                    task_meta=task_meta,
                    **params,
                )
                return await self.wait_for_task(task_id, timeout=timeout, on_progress=on_progress)
            except Exception as e:
                last_error = e
                logger.warning(f"VidGo model {model} failed: {e}")
                if index < len(attempts) - 1:
                    logger.info("VidGo trying fallback model")

        assert last_error is not None
        raise last_error

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
