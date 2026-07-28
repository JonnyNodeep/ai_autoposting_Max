from __future__ import annotations

from typing import Any, Protocol

from app.application.pipeline.context import PipelineContext


class Block(Protocol):
    type_id: str

    async def execute(self, ctx: PipelineContext, config: dict[str, Any]) -> None:
        """Run this block, reading/writing fields on ctx."""
        ...
