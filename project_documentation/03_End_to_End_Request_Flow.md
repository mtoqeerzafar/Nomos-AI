# RagnrAI End-to-End Query Request Flow

## 1. Request Lifecycle Overview

This document provides a step-by-step trace of how a query moves through the RagnrAI platform—from user submission in the web UI, through authentication, multi-layer caching, multi-agent reasoning, verification, certification, and real-time Server-Sent Events (SSE) streaming back to the client.

```
[User UI] ──POST /api/query/stream──> [FastAPI Middleware]
                                             │
                       ┌─────────────────────┴─────────────────────┐
                       ▼                                           ▼
             [Redis Exact Cache]                        [Qdrant Semantic Cache]
             (Check Key: SHA256)                        (Cosine Sim >= 0.96)
                       │                                           │
                       ├─────── (HIT: Return Cached SSE) ──────────┤
                       │                                           │
                       └────────────── (MISS) ─────────────────────┘
                                       │
                                       ▼
                       [LangGraph State Workflow]
                                       │
                 1. Planner Agent ─────┼── Intent & Language Lock
                 2. Query Rewriter ────┼── Context Standalone Query
                 3. Hybrid Retriever ──┼── BGE-M3 Dense + BM25 Sparse
                 4. FlashRank Reranker ┼── Score Calibration (Top 15)
                 5. Relevance Checker ─┼── Sufficiency Pass / Fail
                 6. Candidate Grouper ─┼── Hierarchy Assembly
                 7. Generator Agent ───┼── Evidence Graph Synthesis
                 8. Verifier Agent ────┼── 7-Gate Factual Audit
                 9. Response Composer ─┼── Citation Binding & Warnings
                10. Certification Engine ─ Audit SHA256 Checksum
                                       │
                                       ▼
                       [Cache Write & Response Stream]
```

---

## 2. Step-by-Step Execution Sequence

### Step 1: HTTP API Ingress & Validation
- **Endpoint**: `POST /api/query/stream` or `POST /api/query/v2`
- **Payload**:
  ```json
  {
    "question": "Under Article 78 of Decision 471 of 1995, what conditions apply to private school inmates?",
    "tenant_id": "default_tenant",
    "thread_id": "8dcde63c-1745-4464-b0ec-c3547d61de12",
    "chat_history": []
  }
  ```
- **Operations**:
  1. FastAPI validates input types via Pydantic models.
  2. Evaluates Rate Limiter (`FastAPILimiter` via Redis).
  3. Extracts auth token / tenant headers.

---

### Step 2: Multi-Layer Cache Check
1. **Exact Cache Check**:
   - Computes SHA256 hash of query string.
   - Looks up Redis key `exact_cache:default_tenant:8dcde63c-...:v0:<hash>`.
   - **If Hit**: Immediately streams cached response payload. Execution terminates in $< 15\text{ ms}$.
2. **Semantic Cache Check (if Exact Cache misses)**:
   - Embeds query using BGE-M3 (1024d dense vector).
   - Queries Qdrant collection `semantic_cache` with similarity threshold $\ge 0.96$.
   - **If Hit**: Returns cached answer text and citations. Execution terminates in $< 40\text{ ms}$.

---

### Step 3: LangGraph Agentic Workflow Initialization
Upon cache miss, FastAPI initializes `AgentState` and executes `workflow.astream()`:

```python
initial_state = {
    "question": raw_query,
    "tenant_id": tenant_id,
    "thread_id": thread_id,
    "chat_history": chat_history,
    "documents": [],
    "turn_status": "in_progress"
}
```

---

### Step 4: Agent Node Execution Trace

#### 4.1 Planner Agent (`_plan_step`)
- Input: `question`, `chat_history`.
- Output: `planner_decision` dictionary.
  - `intent`: `"FACT_LOOKUP"`
  - `output_language`: `"English"`
  - `reasoning_strategy`: `"MULTI_ARTICLE"`
  - `extracted_date`: `"1995"`

#### 4.2 Query Rewriter Agent (`_rewrite_step`)
- Input: `question`, `chat_history`, `planner_decision`.
- Output: `current_query` (standalone context-free question).

#### 4.3 Hybrid Retriever (`_retrieve_step`)
- Input: `current_query`, `tenant_id`, `thread_id`.
- Operations: Runs parallel BGE-M3 dense vector search and Qdrant Sparse BM25 search over `ragnr_documents`.
- Merges results using Reciprocal Rank Fusion (RRF):
  $$RRF\_Score(d) = \frac{1}{60 + r_{dense}(d)} + \frac{1}{60 + r_{sparse}(d)}$$
- Output: Top 15 `Document` objects.

#### 4.4 Reranker Engine (`_rerank_step`)
- Input: `documents`, `current_query`.
- Operations: Cross-encoder re-scoring via FlashRank / LLM Reranker.
- Output: Re-ordered `documents` with `rerank_score` (0.0 – 10.0).

#### 4.5 Relevance Checker Engine (`_check_relevance_step`)
- Input: `documents`, `current_query`, `planner_decision`.
- Output: `relevance_result`.
  - `sufficient`: `True`
  - `sufficiency_level`: `"COMPLETE"`
  - `weighted_coverage_score`: `1.0`
- Routing: `should_generate == True` $\rightarrow$ proceed to Evidence Analyzer.

#### 4.6 Candidate Grouper Engine (`_evidence_analyzer_step`)
- Input: `documents`.
- Operations: Groups chunks into document families, resolving law numbers and article keys (`471_1995_78`).
- Output: `candidate_groups`.

#### 4.7 Generator Agent (`_research_step`)
- Input: `documents`, `relevance_result`, `planner_decision`.
- Operations:
  1. Builds `EvidenceReasoningGraph`.
  2. Constructs prompt with target language instruction (`output_language: "English"`).
  3. LLM generates draft answer containing node claims (`[CLAIM:node_id]`).
  4. `ClaimCitationBinder` replaces node claims with exact legal canonical citations (`[المادة 471_1995_78 من القانون 471 لسنة 1995]`).
- Output: `GeneratorOutput` object.

#### 4.8 Verification Agent (`_verification_step`)
- Input: `GeneratorOutput`, `EvidenceReasoningGraph`, `documents`.
- Operations: Runs 7 verification gates (Text Support, Citation Binding, Language Lock, Contradictions, Scope, Amendment Status, Entities).
- Output: `verification_result` (`pass_status: "PASS"`).

#### 4.9 Response Composer (`_response_preparation_step`)
- Input: `GeneratorOutput`, `verification_result`.
- Output: `ResponseOutput` object (structured answer, deduplicated citations, warnings, latency metrics).

#### 4.10 Certification Engine (`_output_guardrail_step`)
- Input: `ResponseOutput`, `documents`, `AgentState`.
- Operations: Calculates SHA256 audit checksum over answer and evidence.
- Output: `CertifiedResponse` object.

---

### Step 5: Cache Write & Response Delivery
1. Saves final verified response to Redis Exact Cache and Qdrant Semantic Cache.
2. Formats SSE data frame:
   ```json
   {
     "event": "message",
     "data": {
       "answer": "Under Article 78 of Ministerial Decision No. 471 of 1995...",
       "citations": ["قرار وزاري رقم (471) لسنة 1995م..."],
       "certified_response": {
         "checksum_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
         "status": "PASS"
       }
     }
   }
   ```
3. Closes SSE stream. Execution completes successfully.
