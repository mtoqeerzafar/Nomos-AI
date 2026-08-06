"""
adapters/flag_embedding_adapter.py
Adapter for FlagEmbedding runtime (native BGE-M3 Dense + Sparse).
"""
from typing import List, Dict, Any
from adapters.base import BaseEmbeddingAdapter

class FlagEmbeddingAdapter(BaseEmbeddingAdapter):
    """FlagEmbedding Runtime Adapter for BGE-M3."""

    def __init__(self, model_id: str, model_string: str = "BAAI/bge-m3"):
        super().__init__(model_id, model_string, "FlagEmbedding")
        self.model = None

    def load(self) -> None:
        from FlagEmbedding import BGEM3FlagModel
        self.model = BGEM3FlagModel(self.model_string, use_fp16=False)
        self.is_loaded = True

    def embed_query(self, query: str) -> Dict[str, Any]:
        if not self.is_loaded:
            self.load()
        res = self.model.encode(query, return_dense=True, return_sparse=True, return_colbert_vecs=False)
        dense_vec = res["dense_vecs"].tolist() if hasattr(res["dense_vecs"], "tolist") else list(res["dense_vecs"])
        lexical_weights = res["lexical_weights"]
        indices = [int(k) for k in lexical_weights.keys()]
        values = [float(v) for v in lexical_weights.values()]
        return {
            "dense": dense_vec,
            "sparse": {"indices": indices, "values": values},
            "dim": len(dense_vec)
        }

    def embed_documents(self, documents: List[str], batch_size: int = 4) -> List[Dict[str, Any]]:
        if not self.is_loaded:
            self.load()
        res = self.model.encode(documents, batch_size=batch_size, return_dense=True, return_sparse=True)
        results = []
        dense_vecs = res["dense_vecs"]
        lexical_weights = res["lexical_weights"]
        for d_vec, l_weights in zip(dense_vecs, lexical_weights):
            indices = [int(k) for k in l_weights.keys()]
            values = [float(v) for v in l_weights.values()]
            results.append({
                "dense": d_vec.tolist(),
                "sparse": {"indices": indices, "values": values}
            })
        return results

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
