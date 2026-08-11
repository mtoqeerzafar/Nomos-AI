# Nomos AI Component Relationships & API Contracts

## 1. Subsystem Interaction Matrix (`Node 0` to `Node 10`)

| Subsystem Component | Interacts With | Interaction Method & Protocol | Data Exchanged |
| :--- | :--- | :--- | :--- |
| **FastAPI Web Server** | Redis Exact Cache | Async Redis Client (`get`/`set`) | SHA256 query key $\leftrightarrow$ Json response payload. |
| **FastAPI Web Server** | Qdrant Semantic Cache | Qdrant Client (`search`/`upsert`) | E5-Large 1024d vector $\leftrightarrow$ Semantic answer match. |
| **FastAPI Web Server** | LangGraph Orchestrator | Async Python Invocation (`astream`) | `AgentState` dictionary $\leftrightarrow$ Stage yield frames. |
| **Node 0 Ingestion Engine** | PostgreSQL & Qdrant | SQLAlchemy & Qdrant Upsert | Raw PDF $\rightarrow$ Chunks, relational tables & payload vectors. |
| **Node 1 Planner Agent** | Azure OpenAI | LLM Chat Completions API | User query $\leftrightarrow$ `PlannerDecision` JSON. |
| **Node 2 Query Rewriter** | Semantic Judge | LLM Chat Completions API | Context query $\leftrightarrow$ `standalone_query` & `retrieval_queries`. |
| **Node 3 Hybrid Retriever** | Qdrant Vector Engine | Qdrant gRPC / HTTP Search API | Query vector + Filters $\leftrightarrow$ Top 75 dense + Top 50 metadata chunks. |
| **Node 4 Candidate Grouper** | Internal Grouper Logic | Sub-Window Merging & SHA256 | 75 raw candidate chunks $\rightarrow$ Top 15–20 consolidated groups. |
| **Node 5 Reranker Agent** | Citation Shield Protocol | Evidence Role Classifier | 15 candidate groups $\rightarrow$ Role-tagged evidence bundle ($\ge 0.0$). |
| **Node 6 Relevance Checker** | 4-Tier Decision Hierarchy | Fast Exit Python + LLM Judge | Evidence bundle $\rightarrow$ `RelevanceDecision v3.1` JSON. |
| **Node 7 Generator Engine** | 5 Sub-Engines & LLM | LLM Chat (`temperature = 0.0`) | `EvidenceReasoningGraph` $\rightarrow$ Draft answer with `[CLAIM:node_id]` tags. |
| **Node 8 Verification Engine**| 7-Gate Audit Guardrail | Deterministic Logic + Micro-Repair | Draft answer $\rightarrow$ `VerificationResult v1.0` (PASS/REPAIRED/FAIL). |
| **Node 9 Response Composer** | 7 Sub-Engines (Zero LLM) | Pure Presentation Engineering | Generator & Verifier artifacts $\rightarrow$ `ResponseOutput v1.0`. |
| **Node 10 Certification** | 6 Sub-Engines (Zero LLM) | Cryptographic SHA256 Engine | `ResponseOutput v1.0` $\rightarrow$ `CertifiedResponse v1.0` & Audit Record. |

---

## 2. Shared Data Contract Schemas

### A. Planner Output Contract (`PlannerDecision`)
```python
class PlannerDecision(BaseModel):
    user_intent: Literal["FACT_LOOKUP", "INTERPRETATION", "COMPARISON", "AMENDMENT_CHECK"]
    reasoning_strategy: Literal["DIRECT", "MULTI_ARTICLE", "AMENDMENT_LOOKUP", "SYNTHESIS"]
    output_language: Literal["Arabic", "English"]
    needs_query_expansion: bool
    extracted_law_number: Optional[str]
    extracted_law_year: Optional[str]
    extracted_articles: List[str]
```

### B. Relevance Decision Contract (`RelevanceDecision v3.1`)
```python
class RelevanceDecision(BaseModel):
    schema_version: str = "3.1"
    checker_version: str = "3.1"
    sufficient: bool
    sufficiency_level: Literal["COMPLETE", "PARTIAL", "INSUFFICIENT"]
    generation_strategy: Literal["COMPLETE", "PARTIAL_WITH_WARNING", "COMPARISON", "LEGAL_EVOLUTION", "REFUSAL_MISSING_CITATION"]
    weighted_coverage_score: float
    evidence_quality_score: float
    retriever_confidence_score: float
    failure_taxonomy: Optional[str]
```

### C. Public Generator Output Contract (`GeneratorOutput v1.1`)
```python
class GeneratorOutput(BaseModel):
    generator_schema_version: str = "1.1"
    generation_strategy_used: str
    structured_answer: str
    claims: List[ClaimBinding]
    citations_bound: List[str]
    unresolved_gaps: List[UnresolvedGap]
    warnings_and_disclaimers: List[str]
    failure_mode: str = "NONE"
```

### D. Verification Result Contract (`VerificationResult v1.0`)
```python
class VerificationResult(BaseModel):
    verification_version: str = "1.0"
    status: Literal["PASS", "PASS_WITH_WARNINGS", "REPAIRED", "FAIL"]
    failure_mode: str = "NONE"
    scores: VerificationScores
    verified_claims: List[VerifiedClaim]
    unsupported_claims: List[VerifiedClaim]
    repair_actions: List[RepairAction]
```

### E. Public Response Contract (`ResponseOutput v1.0`)
```python
class ResponseOutput(BaseModel):
    schema_version: str = "1.0"
    response_status: Literal["ANSWER", "PARTIAL_ANSWER", "REFUSAL"]
    answer_text: str
    citations: List[Citation]
    warnings: List[Warning]
    metadata: ResponseMetadata
```

### F. Certified Response Contract (`CertifiedResponse v1.0`)
```python
class CertifiedResponse(BaseModel):
    contract_version: str = "1.0"
    record_id: str
    checksum: str                                # SHA256 of canonical_json(ResponseOutput)
    certification_status: Literal["CERTIFIED", "CERTIFIED_WITH_WARNINGS", "FAILED"]
    response: ResponseOutput
    audit_telemetry: Dict[str, Any]
```

---

## 3. Failure Handling & Circuit Breaker Logic

1. **Retriever Failure (0 Chunks Found)**:
   - Node 6 (`RelevanceChecker`) detects empty evidence pool.
   - Triggers Tier 1 Fast Exit: `sufficient = False`, `generation_strategy = "REFUSAL_MISSING_CITATION"`.
   - Emits polite legal refusal without calling Node 7 Generator LLM.

2. **Verification Rejection (Major Hallucination Detected)**:
   - If Node 8 (`VerificationEngine`) detects $>50\%$ unsupported claims (`grounding_score < 0.5`), status is set to `FAIL`.
   - Pipeline halts immediately and returns a safe controlled legal refusal.

3. **LLM Provider Timeout / Outage**:
   - Primary: Azure OpenAI (`gpt41mini`).
   - Fallback: `utils.llm_factory` automatically catches Azure exception and switches to round-robin **Groq** (`llama-3.1-8b-instant`).
