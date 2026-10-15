from typing import TYPE_CHECKING, Any

from app.vector.base import EmbeddingModel

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


class LocalEmbeddingModel(EmbeddingModel):
    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model: Any | None = None

    @property
    def model(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self.model_name, device="cpu")
            except Exception as exc:
                raise RuntimeError(
                    "Could not initialize the CPU sentence embedding model. Verify the "
                    "PyTorch, torchvision, transformers, and sentence-transformers versions "
                    "in the image, then rebuild with `docker compose build --no-cache api worker`."
                ) from exc
        return self._model

    def embed(self, text: str) -> list[float]:
        encoded = self.model.encode(text, normalize_embeddings=True)
        return encoded.tolist() if hasattr(encoded, "tolist") else list(encoded)
