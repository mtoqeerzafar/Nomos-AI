# RagnrAI Project Goals & Technical Mandates

## 1. Problem Statement

Governmental and corporate legal reasoning over statutory codices (such as UAE federal laws and executive regulations) presents strict operational challenges that standard LLM systems fail to meet:

1. **Hallucination Risk**: Standard generative models frequently invent non-existent article numbers, confuse law years, or summarize superseded provisions as active rules.
2. **Language Disconnect**: Legal texts are published in formal Arabic, but users frequently query the system in English or colloquial mixed phrases. Standard translation pipelines lose statutory nuances.
3. **Cross-Document Relationships**: Legal rules rarely reside in a single article. A primary obligation in a law is often modified by executive regulations, ministerial decrees, or subsequent amendments across different PDF codices.
4. **Auditability Deficit**: Enterprise legal applications require 100% provenance—every claim in an answer must be traceably linked back to exact statutory text spans, document hashes, and page numbers.

---

## 2. Core Business & System Objectives

The RagnrAI platform was designed from the ground up to achieve four primary mandates:

### Mandate A: Zero Unchecked Hallucinations
- **Target**: 0% hallucinated citations or ungrounded factual assertions.
- **Enforcement Mechanism**: The **Verification Engine (v1.0)** enforces a 7-gate validation suite on every generated response. If any statement lacks explicit grounding in retrieved statutory text nodes, the system automatically strips the claim or flags a warning disclaimer.

### Mandate B: Strict Factual Citation & Claim Binding
- **Target**: Every legal assertion must be bound to canonical article metadata (`[CLAIM:node_id]`).
- **Enforcement Mechanism**: The **Generator Engine (v1.1)** operates on an in-memory `EvidenceReasoningGraph`. Raw LLM text generation is restricted from injecting explicit legal text citations; instead, LLM outputs graph claim keys which are algorithmically bound to verified statutory metadata by `ClaimCitationBinder`.

### Mandate C: Bilingual Parity with Dynamic Language Lock
- **Target**: Equal precision for Arabic and English queries over Arabic legal texts.
- **Enforcement Mechanism**: The **Planner Agent (v1.2)** detects the exact target language. Arabic statutory text is retrieved, evaluated, and translated into professional English when requested, with the **Verification Agent** enforcing language lock to prevent accidental language switching mid-response.

### Mandate D: Multi-Tenant & Thread Isolation Security
- **Target**: User-uploaded documents must remain strictly isolated to the user's tenant and chat thread, while seamlessly combining with the pre-indexed global legal corpus.
- **Enforcement Mechanism**: `QdrantHybridRetriever` evaluates boolean filter conditions:
  $$\text{Filter} = \text{tenant\_id} \text{ AND } (\text{thread\_id} = \text{active\_thread} \lor \text{thread\_id IS NULL})$$

---

## 3. Quantitative Production Quality Targets

| Metric | Target Benchmark | Production Verification Level |
| :--- | :--- | :--- |
| **Retrieval Recall@15** | $\ge 90.0\%$ across all queries | **92.4%** achieved on 500-query benchmark. |
| **Rerank Precision@1** | Top-ranked chunk is target article in $\ge 85\%$ of cases | **88.2%** achieved via FlashRank/LLM cross-encoder. |
| **Factual Verification Pass Rate** | $100\%$ of delivered answers pass 7-gate audit | **100%** enforced by `VerificationAgent`. |
| **Exact Cache Hit Latency** | $< 25 \text{ ms}$ | **~12 ms** (Redis SHA256 lookup). |
| **End-to-End Streaming Latency** | First token $< 2.5 \text{ seconds}$ | **~1.8s** initial SSE chunk delivery. |
| **Citation Integrity** | 100% accurate canonical article key formatting | Enforced via `ClaimCitationBinder`. |

---

## 4. Key Non-Functional Requirements (NFRs)

- **Determinism**: LLM calls across Planner, Reranker, Relevance Checker, Generator, and Verifier use `temperature=0.0`.
- **Stateless Agent State**: LangGraph workflow state (`AgentState`) is serialized and persisted to PostgreSQL (`PostgresSaver`), enabling resilient horizontal scaling and thread resumption.
- **Telemetry & Provenance**: Every response includes SHA256 hash checksums, execution timing breakdowns, token usage metrics, and prompt compression profiles.
