from abc import ABC, abstractmethod

from app.domain.entities.channel import Channel


class ChannelRepository(ABC):
    @abstractmethod
    async def get_by_id(self, channel_id: int) -> Channel | None: ...

    @abstractmethod
    async def get_by_max_chat_id(self, max_chat_id: int) -> Channel | None: ...

    @abstractmethod
    async def get_by_owner(self, owner_id: int) -> list[Channel]: ...

    @abstractmethod
    async def get_all(self) -> list[Channel]: ...

    @abstractmethod
    async def create(self, channel: Channel) -> Channel: ...

    @abstractmethod
    async def update(self, channel: Channel) -> Channel: ...

    @abstractmethod
    async def delete(self, channel_id: int) -> None: ...

    @abstractmethod
    async def count_by_owner(self, owner_id: int) -> int: ...
