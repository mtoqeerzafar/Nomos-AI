# Phase 06 — Reranker Agent & Citation Shield (Node 5)

## 1. Background
Phase 06 calibrates raw retrieval scores by categorizing candidate chunks into statutory **Evidence Roles** and enforcing the **Citation Shield Protocol**.

---

## 2. Goals
- Classify candidate chunks into legal evidence roles (`PRIMARY_OBLIGATION`, `SANCTION_PENALTY`, `EXCEPTION_CLAUSE`, `PROCEDURAL_RULE`, `DEFINITION`).
- Enforce the **Citation Shield Protocol**: Elevates exact article matches (`exact_citation_key == article_key`) to top rank position #1.
- Filter candidates below cutoff threshold (`RERANKER_THRESHOLD >= 0.0`).

---

## 3. Architecture Node Mapping
- **Node Number**: **Node 5** ([`Reranker.md`](file:///d:/RagnrAI/project_documentation/architecture/Reranker.md))
- **Primary Code Location**: `agents/reranker.py`
- **Output Data**: Ordered reranked evidence bundle with role metadata.

---

## 4. Downstream Trajectory
Passes reranked evidence bundle directly to **Node 6** (Relevance Checker Engine).
