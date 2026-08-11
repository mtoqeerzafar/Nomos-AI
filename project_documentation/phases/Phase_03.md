# Phase 03 — Query Rewriter Agent & Memory Resolution (Node 2)

## 1. Background
Phase 03 handles multi-turn conversational ambiguities, pronoun resolution (`it`, `this`), and multi-topic question decomposition into clean retrieval queries.

---

## 2. Goals
- Resolve implicit pronouns (`"What are its penalties?"` $\rightarrow$ `"What are the penalties under Article 78 of Law 471 of 1995?"`).
- Prevent retrieval hallucinations by stripping conversational noise.
- Decompose complex comparison prompts into independent sub-queries (`retrieval_queries`).
- Verify reference resolution via an internal **Semantic Judge** (`_semantic_judge()`).

---

## 3. Architecture Node Mapping
- **Node Number**: **Node 2** ([`Query_Rewriter.md`](file:///d:/RagnrAI/project_documentation/architecture/Query_Rewriter.md))
- **Primary Code Location**: `agents/query_rewriter.py`
- **Output State**: `current_query` and `retrieval_queries` list.

---

## 4. Downstream Trajectory
Passes clean rewritten query payloads directly to **Node 3** (Qdrant Hybrid Retriever).
