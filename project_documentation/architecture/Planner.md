# Architecture Specification: Planner Agent (v1.2)

## 1. Overview
The **Planner Agent (v1.2)** (`agents/planner.py`) is the entry node of the RagnrAI reasoning pipeline. It inspects raw incoming user queries, analyzes historical chat turns, extracts legal metadata constraints, detects target language, and selects the reasoning strategy.

---

## 2. Pydantic Contract (`PlannerDecision`)

```python
class PlannerDecision(BaseModel):
    planner_version: str = Field(default="1.2")
    user_intent: Literal[
        "FACT_LOOKUP", 
        "INTERPRETATION", 
        "COMPARISON", 
        "AMENDMENT_CHECK", 
        "PROCEDURAL_QUERY", 
        "GENERAL"
    ]
    reasoning_strategy: Literal[
        "DIRECT", 
        "MULTI_ARTICLE", 
        "AMENDMENT_LOOKUP", 
        "SYNTHESIS"
    ]
    output_language: Literal["Arabic", "English"]
    extracted_law_number: Optional[str]
    extracted_law_year: Optional[str]
    extracted_articles: List[str]
    extracted_keywords: List[str]
    needs_chat_history: bool
    reasoning: str
```

---

## 3. Algorithm & Logic

1. **Intent Classification**:
   - `FACT_LOOKUP`: Direct queries asking for specific article rules (e.g. "What is Article 4 of Decision 471?").
   - `AMENDMENT_CHECK`: Queries asking whether a law or article was modified by subsequent decrees.
   - `COMPARISON`: Queries comparing provisions across multiple laws.

2. **Reasoning Strategy Routing**:
   - `DIRECT`: Single-article retrieval.
   - `MULTI_ARTICLE`: Multi-article statutory context synthesis.
   - `AMENDMENT_LOOKUP`: Querying law codex + executive regulations in parallel.

3. **Language Lock Detection**:
   - Analyzes character script ratio. If query contains English text (e.g. "Under Article 78..."), sets `output_language: "English"`. If Arabic text, sets `output_language: "Arabic"`.

---

## 4. Inputs & Outputs
- **Input**: `query: str`, `chat_history: List[Dict[str, str]]`
- **Output**: `PlannerDecision` object passed to `AgentState["planner_decision"]`.
