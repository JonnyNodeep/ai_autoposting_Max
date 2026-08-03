from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from app.domain.entities.channel import Channel


ProgressCallback = Callable[[str], Awaitable[None]]


@dataclass
class PipelineContext:
    """Shared state passed through sequential block execution."""

    channel: Channel | None
    channel_link: str
    run_id: int | None
    max_client: Any
    openai_client: Any
    telegram_client: Any = None
    target: Literal["channel", "user"] = "channel"
    target_user_id: int | None = None
    channel_title: str = ""
    on_progress: ProgressCallback | None = None

    image_prompt: str = ""
    image_url: str = ""
    video_token: str = ""
    video_local_path: str = ""
    audio_token: str = ""
    audio_local_path: str = ""
    story_script: str = ""
    post_text: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    # Extra per-run knobs (e.g. model display names for test UX)
    meta: dict[str, Any] = field(default_factory=dict)

    async def notify(self, text: str) -> None:
        if self.on_progress is not None:
            await self.on_progress(text)
