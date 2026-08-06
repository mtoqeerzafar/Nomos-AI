"""
adapters/__init__.py
Export unified embedding adapters.
"""
from adapters.base import BaseEmbeddingAdapter
from adapters.fastembed_adapter import FastEmbedAdapter
from adapters.sentence_transformers_adapter import SentenceTransformersAdapter
from adapters.flag_embedding_adapter import FlagEmbeddingAdapter
from adapters.huggingface_adapter import HuggingFaceAdapter
from adapters.bm25_adapter import BM25Adapter

__all__ = [
    "BaseEmbeddingAdapter",
    "FastEmbedAdapter",
    "SentenceTransformersAdapter",
    "FlagEmbeddingAdapter",
    "HuggingFaceAdapter",
    "BM25Adapter"
]
