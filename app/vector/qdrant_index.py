from typing import Any
from uuid import UUID
import time

from qdrant_client import QdrantClient, models

from app.vector.base import VectorIndex


class QdrantIndex(VectorIndex):
    def __init__(self, url: str, collection: str, dimension: int = 3072):
        self.client = QdrantClient(url=url, timeout=30)
        self.collection = collection
        self.memory_collection = f"{collection}_memory"
        for attempt in range(10):
            try:
                for name in (self.collection, self.memory_collection):
                    if not self.client.collection_exists(name):
                        self.client.create_collection(
                            collection_name=name,
                            vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
                        )
                        if name == self.memory_collection:
                            for field in ("owner_id", "memory_type"):
                                self.client.create_payload_index(
                                    collection_name=name, field_name=field,
                                    field_schema=models.PayloadSchemaType.KEYWORD, wait=True,
                                )
                break
            except Exception:
                if attempt == 9:
                    raise
                time.sleep(2)

    def upsert(self, record_id: str, vector: list[float], payload: dict[str, Any]) -> None:
        # Qdrant accepts UUID strings or unsigned integers as point IDs.
        point_id = str(UUID(hex=record_id[:32]))
        self.upsert_many([(record_id, vector, payload)])

    def upsert_many(self, records: list[tuple[str, list[float], dict[str, Any]]]) -> None:
        if not records:
            return
        points = [
            models.PointStruct(id=str(UUID(hex=record_id[:32])), vector=vector, payload=payload)
            for record_id, vector, payload in records
        ]
        self.client.upsert(collection_name=self.collection, points=points, wait=True)

    def search(self, vector: list[float], limit: int) -> list[dict[str, Any]]:
        hits = self.client.query_points(
            collection_name=self.collection,
            query=vector,
            limit=limit,
            query_filter=models.Filter(must=[models.FieldCondition(key="ready", match=models.MatchValue(value=True))]),
            with_payload=True,
        ).points
        return [{**(hit.payload or {}), "score": hit.score} for hit in hits]

    def delete_document(self, document_id: str) -> None:
        self.client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))])
            ),
            wait=True,
        )

    def activate_document(self, document_id: str) -> None:
        self.client.set_payload(
            collection_name=self.collection,
            payload={"ready": True},
            points=models.FilterSelector(
                filter=models.Filter(must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))])
            ),
            wait=True,
        )

    def upsert_memory(self, memory_id: str, vector: list[float], payload: dict[str, Any]) -> None:
        self.client.upsert(
            collection_name=self.memory_collection,
            points=[models.PointStruct(id=str(UUID(memory_id)), vector=vector, payload=payload)],
            wait=True,
        )

    def search_memories(
        self, vector: list[float], owner_id: str, limit: int = 5, memory_type: str | None = None
    ) -> list[dict[str, Any]]:
        must = [models.FieldCondition(key="owner_id", match=models.MatchValue(value=owner_id))]
        should = None
        if memory_type == "semantic":
            should = [
                models.FieldCondition(key="memory_type", match=models.MatchValue(value=value))
                for value in ("semantic", "user_context")
            ]
        elif memory_type:
            must.append(models.FieldCondition(key="memory_type", match=models.MatchValue(value=memory_type)))
        hits = self.client.query_points(
            collection_name=self.memory_collection,
            query=vector,
            limit=limit,
            query_filter=models.Filter(must=must, should=should),
            with_payload=True,
        ).points
        return [{**(hit.payload or {}), "score": hit.score} for hit in hits]

    def delete_memory(self, memory_id: str) -> None:
        self.client.delete(
            collection_name=self.memory_collection,
            points_selector=[str(UUID(memory_id))],
            wait=True,
        )

    def delete_memories(self, owner_id: str) -> None:
        self.client.delete(
            collection_name=self.memory_collection,
            points_selector=models.FilterSelector(filter=models.Filter(must=[
                models.FieldCondition(key="owner_id", match=models.MatchValue(value=owner_id)),
            ])),
            wait=True,
        )
