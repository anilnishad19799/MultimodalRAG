from abc import ABC, abstractmethod
from typing import Any


class EmbeddingModel(ABC):
    @abstractmethod
    def embed(self, text: str) -> list[float]: ...

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


class VectorIndex(ABC):
    @abstractmethod
    def upsert(self, record_id: str, vector: list[float], payload: dict[str, Any]) -> None: ...

    def upsert_many(self, records: list[tuple[str, list[float], dict[str, Any]]]) -> None:
        for record_id, vector, payload in records:
            self.upsert(record_id, vector, payload)

    @abstractmethod
    def search(self, vector: list[float], limit: int) -> list[dict[str, Any]]: ...

    @abstractmethod
    def delete_document(self, document_id: str) -> None: ...

    @abstractmethod
    def activate_document(self, document_id: str) -> None: ...
