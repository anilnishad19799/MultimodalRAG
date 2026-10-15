from abc import ABC, abstractmethod


class VisionLanguageProvider(ABC):
    @abstractmethod
    def summarize_image(self, image: bytes, mime_type: str, source_hint: str) -> str: ...

    @abstractmethod
    def answer(self, prompt: str, images: list[tuple[bytes, str]]) -> str: ...

    def stream_answer(self, prompt: str, images: list[tuple[bytes, str]]):
        """Stream model output when supported; simple providers can return one chunk."""
        yield self.answer(prompt, images)
