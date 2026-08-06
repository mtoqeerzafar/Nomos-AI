"""
adapters/base.py
Unified Production Embedding Adapter Interface.
All runtimes (FastEmbed, SentenceTransformers, FlagEmbedding, HuggingFace, BM25)
implement this interface to ensure benchmarking scripts interact with a standard API.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class BaseEmbeddingAdapter(ABC):
    """Abstract Base Class for Production Embedding Adapters."""

    def __init__(self, model_id: str, model_string: str, runtime_name: str):
        self.model_id = model_id
        self.model_string = model_string
        self.runtime_name = runtime_name
        self.is_loaded = False

    @abstractmethod
    def load(self) -> None:
        """Load model into memory/device."""
        pass

    @abstractmethod
    def embed_query(self, query: str) -> Dict[str, Any]:
        """
        Generate query vector representation.
        Returns dict:
          {
            "dense": Optional[List[float]],
            "sparse": Optional[Dict[str, List[float]]],  # {"indices": [...], "values": [...]}
            "tokens": Optional[List[str]]                 # for BM25
          }
        """
        pass

    @abstractmethod
    def embed_documents(self, documents: List[str], batch_size: int = 4) -> List[Dict[str, Any]]:
        """
        Generate document vector representations for a list of texts.
        Returns list of dicts with 'dense' and/or 'sparse' keys.
        """
        pass

    @abstractmethod
    def model_info(self) -> Dict[str, Any]:
        """Return model metadata, dimensions, and runtime parameters."""
        pass

    @abstractmethod
    def cleanup(self) -> None:
        """Release memory resources."""
        pass
