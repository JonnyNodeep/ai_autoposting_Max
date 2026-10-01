"""Local motion effects block: gentle Ken Burns zoom (no particle overlays)."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from app.application.pipeline.context import PipelineContext
from app.application.pipeline.postcards.motion import (
    DEFAULT_DURATION_S,
    render_motion_fx,
)
from app.infrastructure.services.openai_client import UPLOAD_DIR


class MotionFxBlock:
    type_id = "motion_fx"

    async def execute(self, ctx: PipelineContext, config: dict[str, Any]) -> None:
        if not config.get("enabled"):
            return
        if not ctx.image_url:
            logger.warning("motion_fx skipped: no image_url run_id={}", ctx.run_id)
            return

        duration_s = float(config.get("duration_s") or DEFAULT_DURATION_S)
        duration_s = max(3.0, min(5.0, duration_s))

        if isinstance(ctx.meta, dict):
            ctx.meta["motion_fx_preset"] = "zoom_only"

        await ctx.notify("✨ Оживляю открытку…")

        local_image = await self._ensure_local_image(ctx)
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        out_path = UPLOAD_DIR / f"motion_fx_{uuid.uuid4().hex}.mp4"
        try:
            render_motion_fx(
                local_image,
                out_path,
                duration_s=duration_s,
                preset_name="zoom_only",
                with_zoom=True,
                use_overlay=False,
                max_zoom=1.06,
            )
        except Exception as e:
            logger.exception("motion_fx render failed run_id={} err={}", ctx.run_id, e)
            raise

        ctx.video_local_path = str(out_path)
        if ctx.max_client is not None:
            token = await ctx.max_client.upload_file(str(out_path), "video")
            ctx.video_token = token
        logger.info(
            "motion_fx ready preset=zoom_only path={} run_id={}",
            out_path,
            ctx.run_id,
        )

    async def _ensure_local_image(self, ctx: PipelineContext) -> Path:
        url = str(ctx.image_url or "")
        if url.startswith("http://") or url.startswith("https://"):
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            dest = UPLOAD_DIR / f"motion_src_{uuid.uuid4().hex}.png"
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                dest.write_bytes(resp.content)
            return dest
        path = Path(url)
        if path.is_file():
            return path
        raise FileNotFoundError(f"motion_fx: image not found: {url}")
