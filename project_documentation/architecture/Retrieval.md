# Architecture Specification: Hybrid Retrieval Subsystem

## 1. Overview
The **Hybrid Retrieval Subsystem** (`retriever/builder.py`) executes concurrent dense and sparse vector retrieval over the Qdrant collection `ragnr_documents`, merging rankings via **Reciprocal Rank Fusion (RRF)** while enforcing multi-tenant isolation rules.

---

## 2. Technical Architecture

- **Dense Search**: BGE-M3 1024-dimensional dense vectors using Cosine distance.
- **Sparse Search**: Token-level BM25 TF-IDF weights computed over Arabic text.
- **Rank Fusion**: RRF constant $k=60$.
- **Candidate Pool**: Top 15 candidate chunks.

---

## 3. Multi-Tenant Boolean Filter Logic

To allow searching both uploaded thread documents and pre-indexed global legal codices, the retriever applies a boolean filter:

$$\text{Filter} = \text{tenant\_id} == \text{active\_tenant} \quad \mathbf{AND} \quad (\text{thread\_id} == \text{active\_thread} \quad \mathbf{OR} \quad \text{thread\_id IS NULL})$$

```python
search_filter = Filter(
    must=[FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))],
    should=[
        FieldCondition(key="thread_id", match=MatchValue(value=thread_id)),
        IsEmptyCondition(key="thread_id")
    ]
)
```

---

## 4. Inputs & Outputs
- **Inputs**: `query: str`, `tenant_id: str`, `thread_id: str`, `top_k: int = 15`
- **Outputs**: Top 15 `Document` objects stored in `AgentState["documents"]`.
