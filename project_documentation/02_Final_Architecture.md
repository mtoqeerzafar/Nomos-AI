# RagnrAI Production Final Architecture

## 1. High-Level Architecture Overview

The RagnrAI platform uses a **stateless API layer**, **distributed multi-layer caching**, **vector and relational storage engines**, and a **10-stage stateful multi-agent reasoning graph**.

```
                           ┌──────────────────────────────┐
                           │   React / Next.js Web UI     │
                           └──────────────┬───────────────┘
                                          │ HTTP / SSE Stream
                                          ▼
                           ┌──────────────────────────────┐
                           │    FastAPI Web Layer         │
                           │  - Auth & Tenant Validation  │
                           │  - Rate Limiting (Redis)     │
                           └──────────────┬───────────────┘
                                          │
                  ┌───────────────────────┴───────────────────────┐
                  ▼                                               ▼
      ┌───────────────────────┐                       ┌───────────────────────┐
      │   Exact Cache (Redis) │                       │  Semantic Cache       │
      │   SHA256 Query Hash   │                       │  Qdrant Vector DB     │
      └───────────┬───────────┘                       └───────────┬───────────┘
                  │ (Hit -> Return)                               │ (Hit -> Return)
                  └───────────────────────┬───────────────────────┘
                                          │ (Miss -> Invoke Workflow)
                                          ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                      LangGraph State Orchestrator (AgentState)                          │
│                                                                                         │
│  [1. Planner] -> [2. Rewriter] -> [3. Hybrid Retriever] -> [4. Reranker]               │
│                                                                    │                    │
│                                                                    ▼                    │
│  [8. Composer] <- [7. Verifier] <- [6. Generator] <- [5. Relevance Checker]             │
│        │                                                                                │
│        ▼                                                                                │
│  [9. Certifier] -> Save Agent Caches -> Stream Response Payload                         │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Shared Agent State Schema (`AgentState`)

The entire multi-agent pipeline communicates through a single, immutable-by-convention LangGraph state dictionary (`AgentState` defined in `agents/workflow.py`).

```python
class AgentState(TypedDict):
    # Core User Inputs
    question: str
    tenant_id: str
    thread_id: str
    chat_history: List[Dict[str, str]]
    
    # Preprocessing & Intent Outputs
    planner_decision: Dict[str, Any]      # Intent, date, domain, language, strategy
    current_query: str                    # Standalone rewritten query string
    retrieval_queries: List[str]          # Multi-query expansion list
    
    # Evidence & Retrieval Payload
    documents: List[Document]             # Retrieved & reranked statutory chunks
    retrieval_trace: Dict[str, Any]       # Dense/sparse timing & score trace
    
    # Agent Decisions & Evidence Graphs
    relevance_result: Dict[str, Any]      # Sufficiency score, coverage, failure taxonomy
    candidate_groups: List[Dict[str, Any]]# Grouped statutory document hierarchies
    generation_artifacts: Dict[str, Any]  # GeneratorOutput & EvidenceReasoningGraph
    verification_result: Dict[str, Any]   # 7-Gate verification report & scores
    
    # Final Certified Output & Telemetry
    response_output: Dict[str, Any]       # ResponseOutput (answer, citations, warnings)
    certified_response: Dict[str, Any]    # CertifiedResponse (SHA256 checksum & audit)
    turn_status: str                      # 'success', 'refusal', 'fallback'
```

---

## 3. Detailed Component Decomposition

### Node 1: Structured Planner (`agents/planner.py`)
- **Version**: `PlannerAgent v1.2`
- **Role**: Parses raw query, extracts temporal legal constraints (e.g. "Law of 1992"), domain filters, target output language ("Arabic" vs "English"), and selects reasoning strategy (`DIRECT`, `MULTI_ARTICLE`, `AMENDMENT_LOOKUP`, `SYNTHESIS`).

### Node 2: Query Rewriter (`agents/rewriter.py`)
- **Version**: `QueryRewriterAgent v1.1`
- **Role**: Resolves conversational pronouns (e.g., "what are its penalties?" $\rightarrow$ "What are penalties under Article 16 of Law 20 of 2018?"), incorporates chat history, and synthesizes standalone retrieval queries.

### Node 3: Hybrid Retriever (`retriever/builder.py`)
- **Version**: `QdrantHybridRetriever`
- **Role**: Executes concurrent hybrid search:
  - **Dense Search**: BGE-M3 (1024 dimensions, cosine distance).
  - **Sparse Search**: Qdrant Sparse BM25 index over tokenized Arabic statutory text.
  - **Reciprocal Rank Fusion (RRF)**: Combines dense and sparse ranks with parameter $k=60$.
  - **Filter**: `tenant_id == active_tenant AND (thread_id == active_thread OR thread_id IS NULL)`.

### Node 4: Evidence Reranker (`agents/reranker.py`)
- **Version**: `FlashRankReranker` / `LLMEvidenceReranker`
- **Role**: Re-scores top-15 retrieved candidate chunks using cross-encoder neural model or LLM relevance scoring, outputting calibrated relevance scores (0.0 to 10.0).

### Node 5: Relevance Checker (`agents/relevance_checker.py`)
- **Version**: `RelevanceChecker v3.1`
- **Role**: Evaluates weighted coverage, metadata integrity, evidence quality, and statutory completeness. Outputs `sufficiency: COMPLETE | PARTIAL | INSUFFICIENT` and sets `should_generate: True/False`.

### Node 6: Candidate Grouper (`agents/candidate_grouper.py`)
- **Version**: `CandidateGrouperEngine`
- **Role**: Organizes chunks into structured statutory hierarchies (Law $\rightarrow$ Executive Regulation $\rightarrow$ Article $\rightarrow$ Clause), resolving parent chunk IDs and cross-document references.

### Node 7: Generator Engine (`agents/generator.py`)
- **Version**: `GeneratorAgent v1.1`
- **Role**: Builds an in-memory `EvidenceReasoningGraph`, applies layout templates based on reasoning strategy, injects language translation rules, generates draft text with claim tags (`[CLAIM:node_id]`), and binds exact legal citations via `ClaimCitationBinder`.

### Node 8: Verification Engine (`agents/verification_agent.py`)
- **Version**: `VerificationAgent v1.0`
- **Role**: Executes 7 deterministic factual verification gates (Text Support, Citation Binding, Language Lock, Contradiction Check, Scope Check, Superseded Law Check, Entity Lock).

### Node 9: Response Composer (`agents/response_composer.py`)
- **Version**: `ResponseComposer v1.0`
- **Role**: Assembles public contract `ResponseOutput` with clean formatted answer, deduplicated citations, warning disclaimers, and execution telemetry metrics.

### Node 10: Certification Engine (`agents/certification_engine.py`)
- **Version**: `CertificationEngine v1.0`
- **Role**: Generates cryptographically secure SHA256 checksum over output text, prompt context, and retrieved chunks, producing the final `CertifiedResponse` payload.

---

## 4. Multi-Layer Caching Architecture

1. **Exact Cache (Redis)**:
   - **Key Format**: `exact_cache:{tenant_id}:{thread_id}:v{version}:{SHA256(query)}`
   - **TTL**: 24 Hours (86,400 seconds)
   - **Lookup Time**: $< 15\text{ ms}$

2. **Semantic Vector Cache (Qdrant)**:
   - **Collection**: `semantic_cache`
   - **Embedding**: BGE-M3 1024d vector of normalized query.
   - **Similarity Threshold**: $\ge 0.96$ cosine similarity.
   - **Metadata Filter**: Matched against `tenant_id` and `thread_id`.
