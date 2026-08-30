from __future__ import annotations

from typing import Any

from loguru import logger

from app.application.pipeline.context import PipelineContext
from app.application.pipeline.tts_voices import (
    DEFAULT_OPENAI_SPEED,
    DEFAULT_OPENAI_VOICE,
    DEFAULT_SPEECHKIT_PITCH_SHIFT,
    DEFAULT_SPEECHKIT_ROLE,
    DEFAULT_SPEECHKIT_SPEED,
    DEFAULT_SPEECHKIT_VOICE,
    DEFAULT_TTS_PROVIDER,
    TTS_PROVIDER_OPENAI,
    TTS_PROVIDER_SPEECHKIT,
    TTS_PROVIDER_SUNOR,
)


class TtsGenBlock:
    type_id = "tts_gen"

    async def execute(self, ctx: PipelineContext, config: dict[str, Any]) -> None:
        if not config.get("enabled"):
            return

        provider = (
            str(config.get("provider") or DEFAULT_TTS_PROVIDER).strip().lower()
            or DEFAULT_TTS_PROVIDER
        )

        if provider == TTS_PROVIDER_SUNOR:
            await self._execute_sunor(ctx)
            return

        script = (ctx.story_script or "").strip() or (ctx.post_text or "").strip()
        if not script:
            logger.warning(f"tts_gen skipped: empty script run_id={ctx.run_id}")
            return

        if provider == TTS_PROVIDER_SPEECHKIT:
            voice = (
                str(config.get("voice") or DEFAULT_SPEECHKIT_VOICE).strip()
                or DEFAULT_SPEECHKIT_VOICE
            )
            try:
                speed = float(config.get("speed", DEFAULT_SPEECHKIT_SPEED))
            except (TypeError, ValueError):
                speed = DEFAULT_SPEECHKIT_SPEED
            try:
                pitch_shift = float(
                    config.get("pitchShift", DEFAULT_SPEECHKIT_PITCH_SHIFT)
                )
            except (TypeError, ValueError):
                pitch_shift = DEFAULT_SPEECHKIT_PITCH_SHIFT
            role = str(config.get("role") or DEFAULT_SPEECHKIT_ROLE).strip() or None

            await ctx.notify(
                f"🎙 Озвучиваю сказку (SpeechKit · {voice}, {speed}, pitch {pitch_shift})…"
            )
            from app.infrastructure.services.yandex_speechkit_client import (
                YandexSpeechKitService,
            )

            path = await YandexSpeechKitService().synthesize(
                script,
                voice=voice,
                speed=speed,
                pitch_shift=pitch_shift,
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

    async def _execute_sunor(self, ctx: PipelineContext) -> None:
        from app.application.pipeline.tale_prompts import STORY_TARGET_CHARS
        from app.application.pipeline.tale_video import (
            TaleGenerationError,
            TaleScript,
            apply_story_length_limit,
            build_tale_video_from_script,
            scenes_from_story,
            truncate_story,
        )

        script = TaleScript.from_meta(
            ctx.meta.get("tale_script") if isinstance(ctx.meta, dict) else None
        )
        if script is None:
            story = (ctx.story_script or "").strip()
            if not story:
                logger.warning(f"tts_gen sunor skipped: empty script run_id={ctx.run_id}")
                return
            if len(story) > STORY_TARGET_CHARS:
                story = truncate_story(story, STORY_TARGET_CHARS)
            caption = (ctx.post_text or story.split("\n", 1)[0])[:500]
            title = (
                (ctx.meta.get("tale_title") if isinstance(ctx.meta, dict) else None)
                or caption[:80]
                or "Сказка"
            )
            script = TaleScript(
                title=str(title)[:120],
                caption=caption,
                story=story,
                scenes=scenes_from_story(story),
            )
            script = apply_story_length_limit(script)
            ctx.meta["tale_script"] = script.to_meta()

        await ctx.notify("🎙 Озвучиваю сказку (Sunor · Suno V5.5) и собираю видео…")
        try:
            result = await build_tale_video_from_script(script)
        except TaleGenerationError as exc:
            logger.error(f"tts_gen sunor failed run_id={ctx.run_id}: {exc}")
            raise

        ctx.video_local_path = result.video_path
        ctx.audio_local_path = result.audio_path
        ctx.audio_token = ""
        ctx.video_token = ""
        if result.caption and not (ctx.post_text or "").strip():
            ctx.post_text = result.caption
        ctx.meta["tale_sunor_task_id"] = result.sunor_task_id
        ctx.meta["tale_scene_count"] = result.scene_count
        logger.info(
            f"tts_gen sunor done video={result.video_path} "
            f"scenes={result.scene_count} run_id={ctx.run_id}"
        )
