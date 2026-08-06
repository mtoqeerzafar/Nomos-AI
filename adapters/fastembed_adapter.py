"""
adapters/fastembed_adapter.py
Adapter for FastEmbed (TextEmbedding and SparseTextEmbedding).
Qualifies models using production client or direct fastembed instances.
"""
import time
from typing import List, Dict, Any, Optional
from adapters.base import BaseEmbeddingAdapter
from db.qdrant_client import qdrant_manager

class FastEmbedAdapter(BaseEmbeddingAdapter):
    """FastEmbed Runtime Adapter."""

    def __init__(self, model_id: str, model_string: str, model_type: str = "dense"):
        super().__init__(model_id, model_string, "FastEmbed")
        self.model_type = model_type.lower()
        self.model_instance = None
        self.dim = None

    def load(self) -> None:
        if self.model_id in ("D1", "S1"):
            qdrant_manager.load_models()
            self._client = qdrant_manager.client
            if self.model_type == "sparse":
                self.model_instance = self._client._get_or_init_sparse_model(self._client.sparse_embedding_model_name)
            else:
                self.model_instance = self._client
            self.is_loaded = True
            self.dim = 1024 if self.model_type == "dense" else None
            return

        if self.model_type == "dense":
            from fastembed import TextEmbedding
            self.model_instance = TextEmbedding(model_name=self.model_string, providers=["CPUExecutionProvider"])
        elif self.model_type == "sparse":
            from fastembed import SparseTextEmbedding
            self.model_instance = SparseTextEmbedding(model_name=self.model_string, providers=["CPUExecutionProvider"])
        else:
            raise ValueError(f"Unsupported model_type: {self.model_type}")
        self.is_loaded = True

    def embed_query(self, query: str) -> Dict[str, Any]:
        if not self.is_loaded:
            self.load()

        if self.model_id == "D1":
            dense_model = self._client._get_or_init_model(self._client.embedding_model_name)
            emb = list(dense_model.embed([query]))[0]
            emb_list = emb.tolist() if hasattr(emb, "tolist") else list(emb)
            return {"dense": emb_list, "sparse": None, "dim": len(emb_list)}

        if self.model_id == "S1":
            sv = list(self.model_instance.query_embed(query))[0]
            indices = list(sv.indices) if hasattr(sv, "indices") else []
            values = list(sv.values) if hasattr(sv, "values") else []
            return {"dense": None, "sparse": {"indices": indices, "values": values}}

        if self.model_type == "dense":
            emb = list(self.model_instance.embed([query]))[0]
            emb_list = emb.tolist() if hasattr(emb, "tolist") else list(emb)
            return {"dense": emb_list, "sparse": None, "dim": len(emb_list)}
        else:
            emb = list(self.model_instance.embed([query]))[0]
            indices = list(emb.indices) if hasattr(emb, "indices") else []
            values = list(emb.values) if hasattr(emb, "values") else []
            return {"dense": None, "sparse": {"indices": indices, "values": values}}

    def embed_documents(self, documents: List[str], batch_size: int = 4) -> List[Dict[str, Any]]:
        if not self.is_loaded:
            self.load()
        results = []
        if self.model_type == "dense":
            embs = list(self.model_instance.embed(documents, batch_size=batch_size))
            for e in embs:
                e_list = e.tolist() if hasattr(e, "tolist") else list(e)
                results.append({"dense": e_list, "sparse": None})
        else:
            embs = list(self.model_instance.embed(documents, batch_size=batch_size))
            for e in embs:
                indices = list(e.indices) if hasattr(e, "indices") else []
                values = list(e.values) if hasattr(e, "values") else []
                results.append({"dense": None, "sparse": {"indices": indices, "values": values}})
        return results

    def model_info(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_string": self.model_string,
            "runtime": self.runtime_name,
            "model_type": self.model_type,
            "is_loaded": self.is_loaded,
            "dim": self.dim
        }

    def cleanup(self) -> None:
        self.model_instance = None
        self.is_loaded = False
