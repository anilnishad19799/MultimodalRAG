import httpx

from app.vector.base import EmbeddingModel


class OpenAIEmbeddingModel(EmbeddingModel):
    def __init__(self, api_key: str, model_name: str, dimension: int, timeout: float = 60.0):
        self.api_key = api_key
        self.model_name = model_name
        self.dimension = dimension
        self.timeout = timeout

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = httpx.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model_name, "input": texts, "dimensions": self.dimension},
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = sorted(response.json()["data"], key=lambda item: item["index"])
            vectors = [item["embedding"] for item in data]
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:1000].replace("\n", " ")
            raise RuntimeError(
                f"OpenAI embedding request failed (HTTP {exc.response.status_code}): {detail}"
            ) from exc
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError(f"OpenAI embedding request failed: {exc}") from exc

        if len(vectors) != len(texts) or any(len(vector) != self.dimension for vector in vectors):
            raise RuntimeError(f"OpenAI returned invalid embeddings; expected {len(texts)} vectors of {self.dimension} dimensions")
        return vectors
