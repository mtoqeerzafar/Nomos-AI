# Architecture Specification: Generator Engine (v1.1)

## 1. Overview
The **Generator Engine (v1.1)** (`agents/generator.py`) synthesizes grounded statutory responses using an in-memory `EvidenceReasoningGraph`. Raw LLM outputs generate claim tags (`[CLAIM:node_id]`) which are bound to verified canonical legal citations via `ClaimCitationBinder`.

---

## 2. EvidenceReasoningGraph Sub-Engine

```python
class EvidenceGraphNode(BaseModel):
    node_id: str
    law_number: str
    law_year: Optional[str]
    article_key: str
    evidence_role: str
    clean_text: str

class EvidenceReasoningGraph(BaseModel):
    nodes: Dict[str, EvidenceGraphNode]
    articles_covered: List[str]
```

---

## 3. Claim Citation Binding Logic

```python
class ClaimCitationBinder:
    @staticmethod
    def bind_claims_and_citations(draft_text: str, graph: EvidenceReasoningGraph):
        # Replaces [CLAIM:node_id] with canonical legal citation formatting
        # e.g., "[المادة 4 من القانون 471 لسنة 1995]"
```

---

## 4. Inputs & Outputs
- **Inputs**: `query: str`, `documents: List[Document]`, `relevance_decision: Dict`, `planner_decision: Dict`
- **Outputs**: `GeneratorOutput` object (structured answer, bound citations, claim bindings).
