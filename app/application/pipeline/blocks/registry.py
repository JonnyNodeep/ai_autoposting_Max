from __future__ import annotations

from typing import Any

from app.application.pipeline.blocks.image_gen import ImageGenBlock
from app.application.pipeline.blocks.image_prompt import ImagePromptBlock
from app.application.pipeline.blocks.post_gen import PostGenBlock
from app.application.pipeline.blocks.story_gen import StoryGenBlock
from app.application.pipeline.blocks.tts_gen import TtsGenBlock
from app.application.pipeline.blocks.video_gen import VideoGenBlock

# Future block types (not implemented yet):
# - content_plan: generate topics/posts from a plan as a pipeline step


class BlockRegistry:
    def __init__(self) -> None:
        self._blocks: dict[str, Any] = {}

    def register(self, block: Any) -> None:
        self._blocks[block.type_id] = block

    def get(self, type_id: str) -> Any | None:
        return self._blocks.get(type_id)

    def known_types(self) -> list[str]:
        return list(self._blocks.keys())


def build_default_registry() -> BlockRegistry:
    registry = BlockRegistry()
    registry.register(StoryGenBlock())
    registry.register(ImagePromptBlock())
    registry.register(ImageGenBlock())
    registry.register(VideoGenBlock())
    registry.register(TtsGenBlock())
    registry.register(PostGenBlock())
    return registry


default_registry = build_default_registry()
