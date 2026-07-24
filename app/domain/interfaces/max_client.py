from abc import ABC, abstractmethod
from typing import Any


class MaxAPIClient(ABC):
    @abstractmethod
    async def send_message(
        self,
        chat_id: int,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
        fmt: str | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def get_messages(self, chat_id: int, count: int = 50) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_chat(self, chat_id: int) -> dict[str, Any]: ...

    @abstractmethod
    async def patch_chat(
        self, chat_id: int, title: str | None = None, icon: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def upload_file(self, file_path: str, file_type: str) -> str: ...

    @abstractmethod
    async def get_me(self) -> dict[str, Any]: ...
