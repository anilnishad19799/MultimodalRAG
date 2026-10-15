from app.core.config import Settings
from app.vector.openai_embeddings import OpenAIEmbeddingModel
from app.vector.qdrant_index import QdrantIndex


def create_vector_dependencies(settings: Settings):
    return OpenAIEmbeddingModel(
        settings.embedding_api_key, settings.embedding_model, settings.embedding_dimension
    ), QdrantIndex(
        settings.qdrant_url, settings.qdrant_collection, settings.embedding_dimension
    )
