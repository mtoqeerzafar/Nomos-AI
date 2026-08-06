# Architecture Specification: Relevance Checker Engine (v3.1)

## 1. Overview
The **Relevance Checker Engine (v3.1)** (`agents/relevance_checker.py`) acts as the statutory quality gate in the RagnrAI pipeline, evaluating retrieved evidence for completeness, weighted article coverage, and metadata integrity before generation is permitted.

---

## 2. Decision Taxonomy & Rules

- **`sufficiency_level`**:
  - `COMPLETE`: Full statutory text present.
  - `PARTIAL`: Core article present, but sub-clause missing.
  - `INSUFFICIENT`: Relevant legal text absent.
- **Forced Rules**:
  - `HIGH_ROLE_COVERAGE_FORCE_PASS`: Triggered when retrieved evidence contains exact statutory article matches with high cross-encoder scores.

---

## 3. Contract Schema (`RelevanceDecision`)

```python
class RelevanceDecision(BaseModel):
    schema_version: str = "3.1"
    checker_version: str = "3.1"
    sufficient: bool
    sufficiency_level: Literal["COMPLETE", "PARTIAL", "INSUFFICIENT"]
    generation_strategy: str
    weighted_coverage_score: float
    evidence_quality_score: float
    retriever_confidence_score: float
    retriever_confidence_level: Literal["HIGH", "MEDIUM", "LOW"]
    failure_taxonomy: Optional[str]
    should_generate: bool
    should_retrieve_again: bool
    reasoning: str
```

---

## 4. Inputs & Outputs
- **Inputs**: `documents: List[Document]`, `query: str`, `planner_decision: Dict[str, Any]`
- **Outputs**: `RelevanceDecision` object dictating workflow branching.
