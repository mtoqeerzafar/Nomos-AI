"""
adapters/bm25_adapter.py
Adapter for BM25 (rank_bm25) in-memory lexical ranking.
"""
from typing import List, Dict, Any
from adapters.base import BaseEmbeddingAdapter

class BM25Adapter(BaseEmbeddingAdapter):
    """BM25 In-Memory Adapter."""

    def __init__(self, model_id: str = "S4", model_string: str = "rank_bm25.BM25Okapi"):
        super().__init__(model_id, model_string, "BM25")
        self.bm25 = None
        self.corpus_keys = []

    def load(self) -> None:
        from rank_bm25 import BM25Okapi
        from db.qdrant_client import qdrant_manager
        qdrant_manager.load_models()
        pts = qdrant_manager.client.scroll("ragnr_documents", limit=300, with_vectors=False)[0]
        corpus_tokens = []
        self.corpus_keys = []
        for p in pts:
            pay = p.payload or {}
            text = pay.get("text") or pay.get("page_content") or ""
            tokens = text.split()
            corpus_tokens.append(tokens)
            self.corpus_keys.append(pay.get("article_key", ""))
        self.bm25 = BM25Okapi(corpus_tokens)
        self.is_loaded = True

    def embed_query(self, query: str) -> Dict[str, Any]:
        if not self.is_loaded:
            self.load()
        tokens = query.split()
        scores = self.bm25.get_scores(tokens)
        nonzero = int((scores > 0).sum())
        return {"dense": None, "sparse": None, "tokens": tokens, "nnz": nonzero}

    def embed_documents(self, documents: List[str], batch_size: int = 4) -> List[Dict[str, Any]]:
        # BM25 is corpus-based, not single-document vector based
        return [{"tokens": doc.split()} for doc in documents]

    def search(self, query: str, limit: int = 30) -> List[Dict[str, Any]]:
        if not self.is_loaded:
            self.load()
        tokens = query.split()
        scores = self.bm25.get_scores(tokens)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:limit]
        return [{"pid": str(i), "article_key": self.corpus_keys[i], "score": float(s)} for i, s in ranked]

    def model_info(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_string": self.model_string,
            "runtime": self.runtime_name,
            "is_loaded": self.is_loaded
        }

    def cleanup(self) -> None:
        self.bm25 = None
        self.corpus_keys = []
        self.is_loaded = False
