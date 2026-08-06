# RagnrAI Component Relationships & API Contracts

## 1. Subsystem Interaction Matrix

| Subsystem Component | Interacts With | Interaction Method & Protocol | Data Exchanged |
| :--- | :--- | :--- | :--- |
| **FastAPI Web Server** | Redis Exact Cache | Async Redis Client (`get`/`set`) | SHA256 query key $\leftrightarrow$ Json response payload. |
| **FastAPI Web Server** | Qdrant Semantic Cache | Qdrant Client (`search`/`upsert`) | BGE-M3 1024d vector $\leftrightarrow$ Semantic answer match. |
| **FastAPI Web Server** | LangGraph Orchestrator | Async Python Invocation (`astream`) | `AgentState` dictionary $\leftrightarrow$ Stage yield frames. |
| **LangGraph Orchestrator** | PostgreSQL Checkpointer | Psycopg3 Connection Pool | State checkpoints & thread history records. |
| **Planner Agent** | Azure OpenAI | LLM Chat Completions API | User query $\leftrightarrow$ `PlannerDecision` JSON. |
| **Hybrid Retriever** | Qdrant Engine | Qdrant gRPC / HTTP Search API | Query vector + Filters $\leftrightarrow$ Top 15 `ScoredPoint` payloads. |
| **Reranker Engine** | Cross-Encoder / LLM | Neural Model Forward Pass | Query + Candidate chunks $\leftrightarrow$ Calibrated relevance scores. |
| **Relevance Checker** | LLM Evaluator | Structured Output JSON | Chunks + Query $\leftrightarrow$ `RelevanceDecision` JSON. |
| **Generator Agent** | Azure OpenAI | LLM Chat Completions API | EvidenceGraph Prompt $\leftrightarrow$ Draft answer with node claims. |
| **Verification Agent** | Factual Audit Engine | Deterministic Rule Logic + LLM | Draft claims + EvidenceGraph $\leftrightarrow$ 7-Gate Audit Report. |
| **Response Composer** | Citation Binder | String Parsing & Regex | Draft text + Canonical Set $\leftrightarrow$ Bound ResponseOutput. |
| **Certification Engine**| Cryptographic Utility | In-Memory SHA256 Hashing | Final answer + Provenance $\leftrightarrow$ `CertifiedResponse`. |

---

## 2. Shared Data Contract Schemas

### A. Planner Output Contract (`PlannerDecision`)
```python
class PlannerDecision(BaseModel):
    user_intent: Literal["FACT_LOOKUP", "INTERPRETATION", "COMPARISON", "AMENDMENT_CHECK"]
    reasoning_strategy: Literal["DIRECT", "MULTI_ARTICLE", "AMENDMENT_LOOKUP", "SYNTHESIS"]
    output_language: Literal["Arabic", "English"]
    extracted_law_number: Optional[str]
    extracted_law_year: Optional[str]
    extracted_articles: List[str]
    needs_chat_history: bool
```

### B. Relevance Decision Contract (`RelevanceDecision`)
```python
class RelevanceDecision(BaseModel):
    sufficient: bool
    sufficiency_level: Literal["COMPLETE", "PARTIAL", "INSUFFICIENT"]
    weighted_coverage_score: float
    evidence_quality_score: float
    generation_strategy: str
    should_generate: bool
    should_retrieve_again: bool
    failure_taxonomy: Optional[str]
```

### C. Public Generator Output Contract (`GeneratorOutput`)
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

### D. Public Verified Response Contract (`ResponseOutput`)
```python
class ResponseOutput(BaseModel):
    response_schema_version: str = "1.0"
    answer_status: Literal["ANSWER", "REFUSAL", "PARTIAL_ANSWER"]
    formatted_answer: str
    citations: List[str]
    warnings_and_disclaimers: List[str]
    confidence_score: float
    verification_passed: bool
    telemetry: Dict[str, Any]
```

---

## 3. Failure Handling & Circuit Breaker Logic

1. **Retriever Failure (0 Chunks Found)**:
   - `RelevanceChecker` detects empty evidence pool.
   - Sets `sufficiency: INSUFFICIENT`, `should_generate: False`.
   - Triggers Global Search Fallback (removes domain/thread filter constraints).
   - If fallback still returns 0 chunks, emits polite legal refusal without calling Generator LLM.

2. **Verification Rejection (Hallucination Detected)**:
   - If `VerificationAgent` fails Gate 1 (Text Support) or Gate 4 (Contradiction), the claim is automatically stripped.
   - If overall support score $< 0.70$, `ResponseComposer` sets `answer_status: PARTIAL_ANSWER` and attaches warning disclaimer.

3. **LLM Provider Timeout / Outage**:
   - Primary: Azure OpenAI (`gpt41mini`).
   - Fallback: `utils.llm_factory` automatically catches Azure exception and switches to round-robin **Groq** (`llama-3.1-8b-instant`).
