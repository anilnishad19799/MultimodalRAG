import sys
from types import ModuleType

from app.vector.local_embeddings import LocalEmbeddingModel


def test_sentence_transformers_import_is_deferred_and_cpu_is_selected(monkeypatch):
    class StubModel:
        def __init__(self, model_name: str, device: str):
            assert model_name == "test-embedder"
            assert device == "cpu"

        def encode(self, text: str, normalize_embeddings: bool):
            assert text == "example"
            assert normalize_embeddings is True
            return [0.25, 0.75]

    stub_module = ModuleType("sentence_transformers")
    stub_module.SentenceTransformer = StubModel
    monkeypatch.setitem(sys.modules, "sentence_transformers", stub_module)

    embedder = LocalEmbeddingModel("test-embedder")
    assert embedder.embed("example") == [0.25, 0.75]
