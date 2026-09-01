from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from app.application.pipeline.context import PipelineContext
from app.infrastructure.services.openai_client import UPLOAD_DIR


class VideoGenBlock:
    type_id = "video_gen"

    async def execute(self, ctx: PipelineContext, config: dict[str, Any]) -> None:
        if not config.get("enabled"):
            return
        video_prompt = (config.get("generated_prompt") or "").strip()
        if not video_prompt or not ctx.image_url:
            return

        from app.infrastructure.services.vidgo_client import VidGoClient

        await ctx.notify("🎬 Загружаю изображение в VidGo...")

        vidgo = VidGoClient()
        try:
            image_url = ctx.image_url
            if not (image_url.startswith("http://") or image_url.startswith("https://")):
                vidgo_image_url = await vidgo.upload_image(image_url)
            else:
                vidgo_image_url = image_url

            task_meta: dict[str, Any] = {
                "channel_link": ctx.channel_link,
            }
            if ctx.target == "user":
                task_meta.update({"kind": "ai_test", "max_user_id": ctx.target_user_id})
            else:
                task_meta.update(
                    {
                        "kind": "pipeline",
                        "run_id": ctx.run_id,
                        "channel_id": ctx.channel.id if ctx.channel else None,
                    }
                )

            await ctx.notify(
                f"🎬 *Генерация видео — {ctx.channel_title}*\n\n"
                f"Статус: обрабатывается...\n"
                f"Это может занять несколько минут."
            )

            async def _on_progress(elapsed: int, _progress: int) -> None:
                if ctx.target == "user":
                    await ctx.notify(
                        f"🎬 *Генерация видео — {ctx.channel_title}*\n\n"
                        f"Генерация: {elapsed // 60} мин..."
                    )

            result = await vidgo.generate_video_with_fallback(
                prompt=video_prompt,
                image_url=vidgo_image_url,
                config=config,
                task_meta=task_meta,
                timeout=900,
                on_progress=_on_progress,
            )
            video_url = result["files"][0]["file_url"]

            if ctx.target == "user":
                await ctx.notify("📥 Скачиваю видео и загружаю в MAX...")

            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as dl_client:
                dl_response = await dl_client.get(video_url)
                dl_response.raise_for_status()
            suffix = Path(video_url).suffix or ".mp4"
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            local_path = UPLOAD_DIR / f"video_{uuid.uuid4().hex[:12]}{suffix}"
            local_path.write_bytes(dl_response.content)

            ctx.video_token = await ctx.max_client.upload_file(str(local_path), "video")
            ctx.video_local_path = str(local_path)
        except Exception as e:
            logger.exception(f"Pipeline video_gen failed run_id={ctx.run_id}: {e}")
            if ctx.target == "user" and ctx.target_user_id is not None:
                await ctx.max_client.send_message_to_user(
                    user_id=ctx.target_user_id,
                    text=f"⚠️ Ошибка генерации видео: {e}",
                )
        finally:
            await vidgo.close()
