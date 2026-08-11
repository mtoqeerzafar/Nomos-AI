# Phase 02 — Planner Agent & Intent Classification (Node 1)

## 1. Background
Phase 02 implements the entry orchestrator for incoming user legal queries, responsible for intent classification, temporal filtering, language script count locking, and strategy routing.

---

## 2. Goals
- Classify user query intent (`FACT_LOOKUP`, `COMPARISON`, `PROCEDURAL`, `AMENDMENT_CHECK`).
- Enforce bilingual script count locking (`arabic_char_count / total_chars`).
- Extract explicit law numbers, years, and article keys.
- Select reasoning strategy (`DIRECT`, `MULTI_ARTICLE`, `AMENDMENT_LOOKUP`, `SYNTHESIS`) and set `needs_query_expansion`.

---

## 3. Architecture Node Mapping
- **Node Number**: **Node 1** ([`Planner.md`](file:///d:/RagnrAI/project_documentation/architecture/Planner.md))
- **Primary Code Location**: `agents/planner.py`
- **Output Contract**: `PlannerDecision` dictionary in `AgentState`.

---

## 4. Execution Logic & Script Count Locking

```python
class PlannerAgent:
    def analyze(self, query: str, chat_history: list) -> dict:
        # Script count language lock
        arabic_chars = len(re.findall(r'[\u0600-\u06FF]', query))
        lang = "Arabic" if (arabic_chars / max(len(query), 1)) > 0.4 else "English"
        
        # LLM intent classification with temperature = 0.0
        ...
```

---

## 5. Downstream Trajectory
If `needs_query_expansion == True`, routes to **Node 2** (Query Rewriter). Otherwise, routes directly to **Node 3** (Qdrant Hybrid Retriever).
