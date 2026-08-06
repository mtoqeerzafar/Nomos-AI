"""
adapters/huggingface_adapter.py
Adapter for HuggingFace Transformers runtime.
Used for custom PyTorch models, custom SPLADE architectures (e.g. Arabic SPLADE),
and raw AutoModel / AutoModelForMaskedLM wrappers.
"""
from typing import List, Dict, Any
from adapters.base import BaseEmbeddingAdapter

class HuggingFaceAdapter(BaseEmbeddingAdapter):
    """HuggingFace Transformers Runtime Adapter."""

    def __init__(self, model_id: str, model_string: str, is_splade: bool = False):
        super().__init__(model_id, model_string, "HuggingFace")
        self.is_splade = is_splade
        self.tokenizer = None
        self.model = None

    def load(self) -> None:
        import torch
        from transformers import AutoTokenizer, AutoModelForMaskedLM, AutoModel
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_string, trust_remote_code=True)
        if self.is_splade:
            try:
                self.model = AutoModelForMaskedLM.from_pretrained(self.model_string, trust_remote_code=True)
            except Exception:
                self.model = AutoModel.from_pretrained(self.model_string, trust_remote_code=True)
        else:
            self.model = AutoModel.from_pretrained(self.model_string, trust_remote_code=True)
        self.model.eval()
        self.is_loaded = True

    def embed_query(self, query: str) -> Dict[str, Any]:
        if not self.is_loaded:
            self.load()
        import torch
        inputs = self.tokenizer(query, return_tensors="pt", truncation=True, max_length=512)
        with torch.no_grad():
            if self.is_splade:
                logits = self.model(**inputs).logits
                # SPLADE max-pooling over sequence
                weights = torch.log(1 + torch.relu(logits[0]))
                weights, _ = torch.max(weights, dim=0)
                nonzero_indices = torch.nonzero(weights).squeeze(-1)
                indices = nonzero_indices.tolist()
                values = weights[nonzero_indices].tolist()
                return {"dense": None, "sparse": {"indices": indices, "values": values}}
            else:
                outputs = self.model(**inputs)
                # Mean pooling
                vec = outputs.last_hidden_state.mean(dim=1).squeeze(0).tolist()
                return {"dense": vec, "sparse": None, "dim": len(vec)}

    def embed_documents(self, documents: List[str], batch_size: int = 4) -> List[Dict[str, Any]]:
        if not self.is_loaded:
            self.load()
        results = []
        for i in range(0, len(documents), batch_size):
            batch = documents[i:i+batch_size]
            for doc in batch:
                results.append(self.embed_query(doc))
        return results

    def model_info(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_string": self.model_string,
            "runtime": self.runtime_name,
            "is_splade": self.is_splade,
            "is_loaded": self.is_loaded
        }

    def cleanup(self) -> None:
        self.tokenizer = None
        self.model = None
        self.is_loaded = False
