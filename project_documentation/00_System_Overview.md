# RagnrAI Production System Overview

## 1. Executive Summary

**RagnrAI** is an enterprise, production-grade **Government Legal Agentic Retrieval-Augmented Generation (RAG) Platform** engineered specifically for authoritative statutory reasoning over United Arab Emirates (UAE) federal laws, ministerial decrees, and executive regulations.

Unlike generic RAG implementations that perform simple vector similarity searches over fragmented chunks, RagnrAI enforces a **zero-hallucination, 100% grounded deterministic multi-agent pipeline**. The system converts raw legal codices into relational statutory evidence graphs, executes hybrid dense/sparse vector retrieval with strict multi-tenant metadata filtering, evaluates retrieval evidence sufficiency via a dedicated Relevance Engine, synthesizes claims bound to canonical article nodes, and validates every generated claim against a 7-gate factual verification guardrail before issuing SHA256 audit-certified responses.

---

## 2. Target Domain & Core Guarantees

- **Closed Legal Corpus**: Operating strictly over official UAE legal codices (e.g. Federal Law No. 43 of 1992, Ministerial Decision No. 471 of 1995, Federal Law No. 21 of 1995, etc.).
- **Bilingual Operation**: Native support for Arabic and English queries with dynamic language lock (Arabic statutory text is accurately translated into English when requested while preserving exact citation keys).
- **Factual Grounding**: Every single legal assertion must map to a verified statutory node (`[CLAIM:node_id]`). Unsupported or hallucinated assertions trigger automatic verification rejection or disclaimer enforcement.
- **Audit Provenance**: Every response produces a verifiable SHA256 checksum, document provenance tree, token telemetry metric, and stage-by-stage latency profile.

---

## 3. Technology Stack

| Layer | Component / Technology | Specification & Role |
| :--- | :--- | :--- |
| **API Web Server** | FastAPI (Python 3.12, Uvicorn) | High-performance async web framework providing REST & Server-Sent Events (SSE) streaming endpoints. |
| **Agentic Framework** | LangGraph & LangChain | Stateful multi-agent workflow orchestration with PostgreSQL checkpointer support. |
| **Primary Vector DB** | Qdrant | Vector engine hosting the `ragnr_documents` collection (BGE-M3 1024-dimensional dense vectors + Qdrant Sparse BM25 indices). |
| **Relational Database** | PostgreSQL (SQLAlchemy & Psycopg3) | Relational persistence for multi-tenant users, chat threads, messages, background ingestion jobs, document relationships, and authority ranks. |
| **Caching Layer** | Redis & Qdrant Vector Cache | SHA256 Exact Query Cache (Redis, 24h TTL) + Cosine Vector Similarity Semantic Cache (Qdrant, 0.96 threshold). |
| **LLM Provider** | Azure OpenAI (GPT-4o / GPT-4o-mini) | Primary LLM inference engine configured with `temperature=0.0` for maximum factual adherence. |
| **Embedding Models** | BGE-M3 (`BAAI/bge-m3`) | Dense multilingual embeddings (1024d) + Sparse BM25 term weights for hybrid retrieval. |
| **Reranking Engine** | FlashRank / Cross-Encoder | Lightweight neural reranking engine for evidence score optimization. |
| **Document Processing** | Docling, PyMuPDF, Struct-Regex | Advanced layout-aware PDF parsing, table extraction, and Arabic structural article boundary chunking. |
| **Async Task Workers** | Celery / Background Workers | Asynchronous PDF document ingestion, OCR preprocessing, and chunk embedding pipelines. |
| **Frontend Interface** | Next.js 14, TailwindCSS, React | Modern, rich web application interface supporting real-time streaming, thread history, and citation overlays. |

---

## 4. Architectural Summary Diagram

```
User Query (Arabic / English)
       │
       ▼
┌────────────────────────────────────────────────────────┐
│               FastAPI API & Web Layer                  │
│  - SSE Streaming Endpoint (/api/query/stream)          │
│  - Redis Exact Cache Check (SHA256 Query Hash)          │
│  - Qdrant Semantic Vector Cache Check (Sim > 0.96)     │
└──────────────────────────┬─────────────────────────────┘
                           │ (Cache Miss)
                           ▼
┌────────────────────────────────────────────────────────┐
│           LangGraph Agentic Orchestrator               │
│                                                        │
│  1. Planner Agent (v1.2)                               │
│     └── Intent Classification & Strategy Routing       │
│                                                        │
│  2. Query Rewriter Agent (v1.1)                        │
│     └── Multi-Turn Memory & Pronoun Resolution         │
│                                                        │
│  3. Qdrant Hybrid Retriever (BGE-M3 Dense + BM25)      │
│     └── Reciprocal Rank Fusion & Metadata Filtering    │
│                                                        │
│  4. Reranker Engine                                    │
│     └── Neural Cross-Encoder Score Calibration         │
│                                                        │
│  5. Relevance Checker Engine (v3.1)                    │
│     └── Sufficiency Scoring & Fallback Control         │
│                                                        │
│  6. Candidate Grouper Engine                           │
│     └── Statutory Hierarchy & Context Assembly         │
│                                                        │
│  7. Generator Agent (v1.1)                             │
│     └── EvidenceReasoningGraph & Draft Synthesis       │
│                                                        │
│  8. Verification Agent (v1.0)                          │
│     └── 7-Gate Factual & Language Audit                │
│                                                        │
│  9. Response Composer (v1.0)                           │
│     └── Citation Binding & Warning Assembly            │
│                                                        │
│ 10. Certification Engine (v1.0)                        │
│     └── SHA256 Audit Hash & Telemetry Sealing          │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│             Cache Write & Response Delivery            │
│  - Save Verified Answer to Redis & Qdrant Cache        │
│  - Return Certified Response Payload to Frontend       │
└────────────────────────────────────────────────────────┘
```

---

## 5. Summary of Key Subsystem Repositories & Packages

- `api/main.py`: FastAPI server setup, lifecycle management, CORS, SSE streaming, thread routes.
- `agents/`: LangGraph workflow definition (`workflow.py`) and agent modules (`planner.py`, `rewriter.py`, `reranker.py`, `relevance_checker.py`, `candidate_grouper.py`, `generator.py`, `verification_agent.py`, `response_composer.py`, `certification_engine.py`).
- `retriever/builder.py`: Multi-tenant hybrid Qdrant search builder with thread-isolation rules.
- `document_processor/`: PDF extraction, normalization, structural article chunking, and batch vector upsert scripts.
- `db/`: SQLAlchemy ORM models (`models.py`), database connection management (`database.py`), and Qdrant client initialization (`qdrant_client.py`).
- `cache/`: Redis SHA256 manager (`exact_cache.py`) and Qdrant semantic vector manager (`semantic_cache.py`).
