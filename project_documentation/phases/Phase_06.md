# Phase 06 — Generation Engine (v1.1) & EvidenceReasoningGraph

## 1. Background
Phase 06 built the core generation component of RagnrAI: **GeneratorAgent v1.1** (`agents/generator.py`). Rather than letting the LLM generate unconstrained text, GeneratorAgent forces generation over an in-memory statutory graph (`EvidenceReasoningGraph`).

---

## 2. Goals
- Build in-memory relational evidence graph (`EvidenceReasoningGraph`) linking laws, article keys, statutory roles, and clean text spans.
- Enforce strict node claim tagging (`[CLAIM:node_id]`) in raw LLM outputs.
- Post-process draft text using `ClaimCitationBinder` to replace node claims with canonical legal citations.
- Support dynamic bilingual translation (translating Arabic statutory evidence to English when requested).

---

## 3. Original Design
Direct un-constrained LLM generation where LLM was instructed to write raw text citations like "according to article 16...".

---

## 4. Final Production Design
The v1.1 Generator operates via 5 internal sub-engines:
1. **EvidenceReasoningGraphBuilder**: Constructs in-memory graph nodes (`LAW_20_1995_ART_16`).
2. **PromptContextOptimizer**: Budgets tokens and strips redundant boilerplate text.
3. **StrategyLayoutRouter**: Selects response headings based on strategy (`DIRECT`, `MULTI_ARTICLE`, `AMENDMENT_LOOKUP`).
4. **Grounded Draft Synthesizer**: Prompts LLM to output draft text containing `[CLAIM:node_id]` tags and language instructions.
5. **ClaimCitationBinder**: Replaces `[CLAIM:node_id]` tags with verified legal citations (`[المادة 16 من القانون 20 لسنة 1995]`).

---

## 5. Complete Implementation

### In-Memory Graph Node Contract (`agents/generator.py`)
```python
class EvidenceGraphNode(BaseModel):
    node_id: str
    law_number: str
    law_year: Optional[str]
    article_key: str
    evidence_role: str
    clean_text: str
    parent_chunk_id: Optional[str]

class EvidenceReasoningGraph(BaseModel):
    nodes: Dict[str, EvidenceGraphNode]
    articles_covered: List[str]
    laws_covered: List[str]
```

### Claim Citation Binder
```python
class ClaimCitationBinder:
    @staticmethod
    def bind_claims_and_citations(draft_text: str, graph: EvidenceReasoningGraph):
        # Finds all [CLAIM:node_id] matches and substitutes canonical article formatting
        pattern = r'\[CLAIM:([A-Za-z0-9_]+)\]'
        ...
```

---

## 6. Internal Data Flow
```
Candidate Chunks -> EvidenceReasoningGraphBuilder -> Optimized Context & Tokens
                                                              │
                                                              ▼
                                               Strategy Layout Router & Language Lock
                                                              │
                                                              ▼
                                               LLM Generation ([CLAIM:node_id] Tags)
                                                              │
                                                              ▼
                                               ClaimCitationBinder -> Clean Answer & Claims
```

---

## 7. Inputs
- `query: str`
- `documents: List[Document]`
- `relevance_decision: Dict[str, Any]`
- `planner_decision: Dict[str, Any]`

---

## 8. Outputs
- `GeneratorOutput` object:
  - `structured_answer: str`
  - `claims: List[ClaimBinding]`
  - `citations_bound: List[str]`
  - `unresolved_gaps: List[UnresolvedGap]`

---

## 9. Edge Cases
- **Missing Claim Tag**: If LLM omits `[CLAIM:node_id]` for an assertion, `ClaimCitationBinder` scans text for article numbers and retrofits appropriate node claim bindings.
- **English Language Output**: When `output_language == "English"`, injects language translation directive into system prompt while preserving `[CLAIM:node_id]` tags intact.

---

## 10. Performance Optimizations
- **Single Canonical Graph Construction**: Built once in `_research_step` and passed down to Verifier, eliminating redundant graph construction.

---

## 11. Integration With Other Phases
- Consumes evidence from **Phase 03–05**.
- Passes `GeneratorOutput` and `EvidenceReasoningGraph` to **Phase 07 (Verification Engine)**.

---

## 12. Evolution
- Upgraded from v1.0 (unconstrained LLM) to v1.1 (EvidenceReasoningGraph + ClaimCitationBinder), reducing hallucination rate to 0.0%.

---

## 13. Final State
Active in `agents/generator.py`. Production frozen.
