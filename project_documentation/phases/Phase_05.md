# Phase 05 — Candidate Grouper Engine (Node 4)

## 1. Background
Phase 05 consolidates raw multi-provider retrieval hits (75 chunks) down to a non-redundant, unified statutory evidence list (Top 15–20).

---

## 2. Goals
- **Sub-Window Merging (`_group_by_article_key`)**: Staples multi-window sliding chunks (`Parts 1, 2, 3` of Article 78) sharing `article_key = "471_1995_78"` back into a single unified article text block.
- **Fast Hash Deduplication (`SHA256 Content Hashing`)**: Generates 64-character digital fingerprints of text content to purge duplicate passages.
- **Max Score Aggregation (`score_aggregation = "max"`)**: Assigns maximum relevance score (`0.94`) of sub-windows to the combined article block.

---

## 3. Architecture Node Mapping
- **Node Number**: **Node 4** ([`Candidate_Grouper.md`](file:///d:/RagnrAI/project_documentation/architecture/Candidate_Grouper.md))
- **Primary Code Location**: `retriever/grouping.py`
- **Output Data**: Top 15–20 consolidated candidate groups.

---

## 4. Downstream Trajectory
Passes consolidated candidate groups directly to **Node 5** (Reranker Agent).
