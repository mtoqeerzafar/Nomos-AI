"""
adapters/sentence_transformers_adapter.py
Adapter for SentenceTransformers runtime.
Fallback runtime for dense models (e5-large, BGE-M3).
"""
from typing import List, Dict, Any
from adapters.base import BaseEmbeddingAdapter

class SentenceTransformersAdapter(BaseEmbeddingAdapter):
    """SentenceTransformers Runtime Adapter."""

    def __init__(self, model_id: str, model_string: str):
        super().__init__(model_id, model_string, "SentenceTransformers")
        self.model = None

    def load(self) -> None:
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(self.model_string, device="cpu")
        self.is_loaded = True

    def embed_query(self, query: str) -> Dict[str, Any]:
        if not self.is_loaded:
            self.load()
        # Add query prefix if required for e5 models
        text = f"query: {query}" if "e5" in self.model_string.lower() else query
        vec = self.model.encode(text, convert_to_numpy=True).tolist()
        return {"dense": vec, "sparse": None, "dim": len(vec)}

    def embed_documents(self, documents: List[str], batch_size: int = 4) -> List[Dict[str, Any]]:
        if not self.is_loaded:
            self.load()
        texts = [f"passage: {doc}" if "e5" in self.model_string.lower() else doc for doc in documents]
        vecs = self.model.encode(texts, batch_size=batch_size, convert_to_numpy=True)
        return [{"dense": v.tolist(), "sparse": None} for v in vecs]

    def model_info(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_string": self.model_string,
            "runtime": self.runtime_name,
            "is_loaded": self.is_loaded
        }

    def cleanup(self) -> None:
        self.model = None
        self.is_loaded = False
