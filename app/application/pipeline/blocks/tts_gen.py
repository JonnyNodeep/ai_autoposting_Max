from __future__ import annotations

from typing import Any

from loguru import logger

from app.application.pipeline.context import PipelineContext
from app.application.pipeline.tts_voices import (
    DEFAULT_OPENAI_SPEED,
    DEFAULT_OPENAI_VOICE,
    DEFAULT_SPEECHKIT_ROLE,
    DEFAULT_SPEECHKIT_SPEED,
    DEFAULT_SPEECHKIT_VOICE,
    DEFAULT_TTS_PROVIDER,
    TTS_PROVIDER_SPEECHKIT,
)


class TtsGenBlock:
    type_id = "tts_gen"

    async def execute(self, ctx: PipelineContext, config: dict[str, Any]) -> None:
        if not config.get("enabled"):
            return

        script = (ctx.story_script or "").strip() or (ctx.post_text or "").strip()
        if not script:
            logger.warning(f"tts_gen skipped: empty script run_id={ctx.run_id}")
            return

        provider = (
            str(config.get("provider") or DEFAULT_TTS_PROVIDER).strip().lower()
            or DEFAULT_TTS_PROVIDER
        )

        if provider == TTS_PROVIDER_SPEECHKIT:
            voice = (
                str(config.get("voice") or DEFAULT_SPEECHKIT_VOICE).strip()
                or DEFAULT_SPEECHKIT_VOICE
            )
            try:
                speed = float(config.get("speed", DEFAULT_SPEECHKIT_SPEED))
            except (TypeError, ValueError):
                speed = DEFAULT_SPEECHKIT_SPEED
            role = str(config.get("role") or DEFAULT_SPEECHKIT_ROLE).strip() or None

            await ctx.notify(f"🎙 Озвучиваю сказку (SpeechKit · {voice}, {speed})…")
            from app.infrastructure.services.yandex_speechkit_client import (
                YandexSpeechKitService,
            )

            path = await YandexSpeechKitService().synthesize(
                script,
                voice=voice,
                speed=speed,
                role=role,
            )
        else:
            model = (
                (config.get("model") or "gpt-4o-mini-tts").strip() or "gpt-4o-mini-tts"
            )
            voice = (
                str(config.get("voice") or DEFAULT_OPENAI_VOICE).strip()
                or DEFAULT_OPENAI_VOICE
            )
            try:
                speed = float(config.get("speed", DEFAULT_OPENAI_SPEED))
            except (TypeError, ValueError):
                speed = DEFAULT_OPENAI_SPEED
            response_format = (config.get("response_format") or "mp3").strip() or "mp3"
            instructions = (config.get("instructions") or "").strip() or None

            await ctx.notify(f"🎙 Озвучиваю сказку (OpenAI · {voice}, {speed})…")
            path = await ctx.openai_client.generate_speech(
                script,
                model=model,
                voice=voice,
                speed=speed,
                response_format=response_format,
                instructions=instructions,
            )

        ctx.audio_local_path = path
        ctx.audio_token = ""
        logger.info(
            f"tts_gen done provider={provider} path={path} run_id={ctx.run_id}"
        )
