from __future__ import annotations

from typing import Any

from loguru import logger

from app.application.pipeline.context import PipelineContext


class TtsGenBlock:
    type_id = "tts_gen"

    async def execute(self, ctx: PipelineContext, config: dict[str, Any]) -> None:
        if not config.get("enabled"):
            return

        script = (ctx.story_script or "").strip() or (ctx.post_text or "").strip()
        if not script:
            logger.warning(f"tts_gen skipped: empty script run_id={ctx.run_id}")
            return

        model = (config.get("model") or "tts-1-hd").strip() or "tts-1-hd"
        voice = (config.get("voice") or "shimmer").strip() or "shimmer"
        try:
            speed = float(config.get("speed", 0.85))
        except (TypeError, ValueError):
            speed = 0.85
        response_format = (config.get("response_format") or "mp3").strip() or "mp3"

        await ctx.notify(
            f"🎙 Озвучиваю сказку ({voice}, {speed})…"
        )
        path = await ctx.openai_client.generate_speech(
            script,
            model=model,
            voice=voice,
            speed=speed,
            response_format=response_format,
        )
        ctx.audio_local_path = path
        ctx.audio_token = ""
        logger.info(f"tts_gen done path={path} run_id={ctx.run_id}")
