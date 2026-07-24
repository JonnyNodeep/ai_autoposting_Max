from abc import ABC, abstractmethod

from app.domain.entities.content_plan import ContentPlan
from app.domain.entities.content_topic import ContentTopic
from app.domain.entities.content_post import ContentPost


class ContentPlanRepository(ABC):
    @abstractmethod
    async def get_by_id(self, plan_id: int) -> ContentPlan | None: ...

    @abstractmethod
    async def get_by_channel(self, channel_id: int) -> list[ContentPlan]: ...

    @abstractmethod
    async def create(self, plan: ContentPlan) -> ContentPlan: ...

    @abstractmethod
    async def update(self, plan: ContentPlan) -> ContentPlan: ...

    @abstractmethod
    async def delete(self, plan_id: int) -> None: ...


class ContentTopicRepository(ABC):
    @abstractmethod
    async def get_by_id(self, topic_id: int) -> ContentTopic | None: ...

    @abstractmethod
    async def get_by_plan(self, plan_id: int) -> list[ContentTopic]: ...

    @abstractmethod
    async def create(self, topic: ContentTopic) -> ContentTopic: ...

    @abstractmethod
    async def create_batch(self, topics: list[ContentTopic]) -> list[ContentTopic]: ...

    @abstractmethod
    async def update(self, topic: ContentTopic) -> ContentTopic: ...

    @abstractmethod
    async def delete(self, topic_id: int) -> None: ...

    @abstractmethod
    async def reorder(self, topic_id: int, new_order: int) -> None: ...


class ContentPostRepository(ABC):
    @abstractmethod
    async def get_by_id(self, post_id: int) -> ContentPost | None: ...

    @abstractmethod
    async def get_by_topic(self, topic_id: int) -> ContentPost | None: ...

    @abstractmethod
    async def create(self, post: ContentPost) -> ContentPost: ...

    @abstractmethod
    async def update(self, post: ContentPost) -> ContentPost: ...
