# Phase 05 — Relevance Checker Engine (v3.1)

## 1. Background
Generating answers over incomplete or non-relevant retrieval evidence leads directly to hallucinations. Phase 05 introduced the **Relevance Checker Engine (v3.1)** (`agents/relevance_checker.py`), which acts as an autonomous gatekeeper evaluating evidence sufficiency before generation is permitted.

---

## 2. Goals
- Compute a deterministic weighted sufficiency score based on article coverage, metadata integrity, and cross-encoder rerank scores.
- Classify sufficiency as `COMPLETE`, `PARTIAL`, or `INSUFFICIENT`.
- Direct workflow routing (`should_generate: True/False`, `should_retrieve_again: True/False`).

---

## 3. Original Design
Simple LLM binary prompt asking "Is this context relevant to the user question? Yes or No."

---

## 4. Final Production Design
The v3.1 Relevance Checker combines a **deterministic scoring matrix** with a structured LLM audit call. It evaluates:
- Weighted Article Coverage Score
- Metadata Integrity Score
- Rerank Confidence Thresholds
- High Role Coverage Forced Pass Rules (`HIGH_ROLE_COVERAGE_FORCE_PASS`)

---

## 5. Complete Implementation

### Weighted Coverage Formula
$$\text{Weighted Score} = 0.4 \times \text{Coverage} + 0.3 \times \text{Integrity} + 0.3 \times \text{RerankScore}$$

### Contract Schema (`agents/relevance_checker.py`)
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

## 6. Internal Data Flow
```
Retrieved Candidate Chunks + Standalone Query + Planner Intent
                             │
                             ▼
            Deterministic Metric Evaluator
      (Coverage, Rerank Scores, Metadata Check)
                             │
                             ▼
             LLM Evidence Sufficiency Audit
                             │
                             ▼
         Combine Scores & Apply Failure Taxonomy
                             │
                             ▼
        Output RelevanceDecision (should_generate)
```

---

## 7. Inputs
- Candidate `Document` objects.
- `current_query: str`
- `planner_decision: Dict[str, Any]`

---

## 8. Outputs
- `RelevanceDecision` object dictating whether workflow proceeds to **Generator Agent** or triggers **Fallback / Refusal**.

---

## 9. Edge Cases
- **Low-Confidence Retrieval**: If composite score $< 0.45$, `should_generate` is set to `False`, triggering Global Search Fallback.
- **Empty Retrieval Pool**: Instantly outputs `sufficiency_level: INSUFFICIENT` and `failure_taxonomy: NO_RETRIEVAL` without invoking LLM.

---

## 10. Performance Optimizations
- Fast short-circuit evaluation: If deterministic rules trigger `HIGH_ROLE_COVERAGE_FORCE_PASS`, LLM sufficiency evaluation is bypassed.

---

## 11. Integration With Other Phases
- Evaluates output from **Phase 03 (Retriever)** and **Phase 04 (Grouper)**.
- Controls entry into **Phase 06 (Generator Engine)** or fallback.

---

## 12. Evolution
- Replaced fragile binary LLM prompts with dual deterministic + LLM v3.1 hybrid evaluation.

---

## 13. Final State
Active in `agents/relevance_checker.py`. Production frozen.
