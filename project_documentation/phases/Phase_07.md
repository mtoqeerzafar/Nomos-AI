# Phase 07 — Relevance Checker Engine (Node 6)

## 1. Background
Phase 07 acts as the Quality Gatekeeper & Sufficiency Audit Gate of Nomos AI, inspecting evidence completeness before allowing generation.

---

## 2. Goals
- Execute a **4-Tier Decision Hierarchy**:
  - **Tier 1**: Citation Match Check (Fast Exit rule, 0 tokens spent).
  - **Tier 2**: Coverage Score Check (`weighted_coverage_score >= 0.70`).
  - **Tier 3**: Role Completeness Check (`PRIMARY_OBLIGATION` presence).
  - **Tier 4**: LLM Sufficiency Audit (Fallback for multi-law comparison queries).
- Output sufficiency level (`COMPLETE`, `PARTIAL`, `INSUFFICIENT`) and generation strategy (`COMPLETE`, `PARTIAL_WITH_WARNING`, `REFUSAL_MISSING_CITATION`).

---

## 3. Architecture Node Mapping
- **Node Number**: **Node 6** ([`Relevance_Checker.md`](file:///d:/RagnrAI/project_documentation/architecture/Relevance_Checker.md))
- **Primary Code Location**: `agents/relevance_checker.py`
- **Output Contract**: `RelevanceDecision v3.1`.

---

## 4. Downstream Trajectory
If sufficient, routes directly to **Node 7** (Generator Engine v1.1). If missing cited articles, triggers controlled refusal.
