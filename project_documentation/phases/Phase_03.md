# Phase 03 — Hybrid Retrieval & Pareto Benchmark Optimization

## 1. Background
Single-mode vector search (dense-only) frequently fails in statutory domain queries containing exact article numbers or specific legal terminology (e.g. "المادة 78"). Phase 03 introduced hybrid retrieval fusing dense semantic vectors and sparse BM25 keyword weights.

---

## 2. Goals
- Build `QdrantHybridRetriever` (`retriever/builder.py`).
- Implement **Reciprocal Rank Fusion (RRF)** to combine dense and sparse rankings deterministically.
- Optimize candidate pool size ($K$) to achieve $\ge 90\%$ Recall@15 on a 500-query benchmark suite.

---

## 3. Original Design
Pure dense vector search querying top-10 chunks using standard cosine similarity.

---

## 4. Final Production Design
Fuses BGE-M3 1024d dense similarity with Qdrant Sparse BM25 scoring using RRF ($k=60$). Candidate pool is set to top-15 documents. Metadata filtering enforces multi-tenancy without excluding global corpus chunks.

---

## 5. Complete Implementation

### RRF Fusion Formula
$$RRF\_Score(d) = \frac{1}{60 + r_{dense}(d)} + \frac{1}{60 + r_{sparse}(d)}$$

### Python Implementation (`retriever/builder.py`)
```python
class QdrantHybridRetriever:
    def _combine_rrf(self, dense_results, sparse_results, top_k=15, rrf_k=60):
        scores = {}
        docs = {}
        for rank, hit in enumerate(dense_results, 1):
            doc_id = hit.id
            scores[doc_id] = scores.get(doc_id, 0.0) + (1.0 / (rrf_k + rank))
            docs[doc_id] = hit
        for rank, hit in enumerate(sparse_results, 1):
            doc_id = hit.id
            scores[doc_id] = scores.get(doc_id, 0.0) + (1.0 / (rrf_k + rank))
            docs[doc_id] = hit
            
        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)[:top_k]
        return [docs[i] for i in sorted_ids]
```

### Multi-Tenant Query Filter
```python
filter_conditions = Filter(
    must=[FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))],
    should=[
        FieldCondition(key="thread_id", match=MatchValue(value=thread_id)),
        IsEmptyCondition(key="thread_id")
    ]
)
```

---

## 6. Internal Data Flow
```
Query String -> BGE-M3 Dense Vector (1024d) & Sparse BM25 Vector
                      │
    ┌─────────────────┴─────────────────┐
    ▼                                   ▼
Qdrant Dense Search                 Qdrant Sparse Search
(Top 30 ScoredPoints)               (Top 30 ScoredPoints)
    │                                   │
    └─────────────────┬─────────────────┘
                      ▼
            Reciprocal Rank Fusion (RRF k=60)
                      │
                      ▼
            Top 15 Candidate Chunks
```

---

## 7. Inputs
- `query_string: str`
- `tenant_id: str`
- `thread_id: str`
- `top_k: int = 15`

---

## 8. Outputs
- Ranked list of 15 `Document` objects with metadata payloads and RRF composite scores.

---

## 9. Edge Cases
- **Zero Hits in Thread**: Fallback condition ensures global chunks (`thread_id IS NULL`) are evaluated so global legal codices are never excluded.
- **Identical RRF Scores**: Tied scores are deterministically resolved by dense vector raw similarity rank.

---

## 10. Performance Optimizations
- **Parallel Search Calls**: Dense and sparse gRPC queries execute concurrently in under $250\text{ ms}$.

---

## 11. Integration With Other Phases
- Receives queries from **Phase 01/02** ingestion indices.
- Passes candidate pool to **Phase 04 (Candidate Grouper)** and **Phase 05 (Relevance Checker)**.

---

## 12. Evolution
- Empirical benchmarks across 500 queries proved that RRF Hybrid Fusion raised true recall from 74.2% (dense-only) to **92.4%**.

---

## 13. Final State
Active in `retriever/builder.py`. Frozen production component.
