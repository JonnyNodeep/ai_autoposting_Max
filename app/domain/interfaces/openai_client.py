from abc import ABC, abstractmethod


class OpenAIClient(ABC):
    @abstractmethod
    async def generate_text(self, prompt: str, system_prompt: str | None = None) -> str: ...

    @abstractmethod
    async def generate_image(self, prompt: str, channel_link: str | None = None) -> str: ...

    @abstractmethod
    async def analyze_vision(self, prompt: str, base64_images: list[str]) -> str: ...

    @abstractmethod
    async def search_web(self, query: str) -> str: ...
