from abc import ABC, abstractmethod


class OpenAIClient(ABC):
    @abstractmethod
    async def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        *,
        model: str | None = None,
    ) -> str: ...

    @abstractmethod
    async def generate_image(self, prompt: str) -> str: ...

    @abstractmethod
    async def analyze_vision(self, prompt: str, base64_images: list[str]) -> str: ...

    @abstractmethod
    async def search_web(self, query: str) -> str: ...

    @abstractmethod
    async def generate_speech(
        self,
        text: str,
        *,
        model: str | None = None,
        voice: str = "shimmer",
        speed: float = 0.85,
        response_format: str = "mp3",
        instructions: str | None = None,
    ) -> str: ...
