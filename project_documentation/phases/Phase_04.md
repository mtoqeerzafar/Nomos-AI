# Phase 04 — Qdrant Hybrid Retriever (Node 3)

## 1. Background
Phase 04 executes high-precision dual-provider evidence retrieval combining dense vector similarity with exact statutory payload index matching over Qdrant collections.

---

## 2. Goals
- Execute **Dense Vector Search**: Top-75 candidates using `intfloat/multilingual-e5-large` 1024d embeddings (`VECTOR_SEARCH_K = 75`).
- Execute **Exact Statutory Metadata Search**: Top-50 candidates querying Qdrant payload indices (`law_number`, `law_year`, `article_number`, `article_key`).
- Perform **Smart Statutory Neighbor Expansion** to pull adjacent articles.
- Enforce boolean multi-tenant isolation filters.

---

## 3. Architecture Node Mapping
- **Node Number**: **Node 3** ([`Retrieval.md`](file:///d:/RagnrAI/project_documentation/architecture/Retrieval.md))
- **Primary Code Location**: `retriever/builder.py`
- **Output Data**: 75 raw candidate `Document` objects.

---

## 4. Downstream Trajectory
Passes 75 raw candidate chunks directly to **Node 4** (Candidate Grouper Engine).
