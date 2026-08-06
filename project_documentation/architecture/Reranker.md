# Architecture Specification: Reranker Engine

## 1. Overview
The **Reranker Engine** (`agents/reranker.py`) re-scores the 15 candidate chunks fetched by the Hybrid Retriever using a neural cross-encoder model (**FlashRank**) or LLM evidence cross-evaluator, calibrating relevance scores to a normalized $0.0 - 10.0$ scale.

---

## 2. Reranking Architecture & Formula

Given query $Q$ and candidate statutory chunk $C_i$:
$$\text{Score}(Q, C_i) = \text{CrossEncoder}(Q, C_i)$$

Chunks are sorted descending by `rerank_score`. The top chunk (`Top 1`) is evaluated for statutory relevance.

---

## 3. Implementation Code

```python
class FlashRankReranker:
    def rerank(self, query: str, documents: List[Document], top_n: int = 15) -> List[Document]:
        # Cross-encoder inference pass over candidate texts
        pass_input = [{"query": query, "text": d.page_content} for d in documents]
        results = self.ranker.rank(pass_input)
        
        reranked_docs = []
        for res in results[:top_n]:
            doc = documents[res["id"]]
            doc.metadata["rerank_score"] = float(res["score"])
            reranked_docs.append(doc)
        return reranked_docs
```

---

## 4. Inputs & Outputs
- **Inputs**: `query: str`, `documents: List[Document]`
- **Outputs**: Re-ordered `documents` list with `rerank_score` injected into metadata.
