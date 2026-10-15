from dataclasses import dataclass

from app.core.config import Settings
from app.providers.base import VisionLanguageProvider
from app.providers.factory import create_provider
from app.storage.base import ObjectStorage
from app.storage.factory import create_storage
from app.vector.base import EmbeddingModel, VectorIndex
from app.vector.factory import create_vector_dependencies


@dataclass
class Services:
    provider: VisionLanguageProvider
    storage: ObjectStorage
    embedder: EmbeddingModel
    vector_index: VectorIndex


def build_services(settings: Settings) -> Services:
    embedder, vector_index = create_vector_dependencies(settings)
    return Services(
        provider=create_provider(settings),
        storage=create_storage(settings),
        embedder=embedder,
        vector_index=vector_index,
    )
