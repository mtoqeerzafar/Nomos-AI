# System Diagrams: Master Nomos AI 11-Node Pipeline

## 1. Master Nomos AI 11-Node Agentic Pipeline Flowchart

```mermaid
flowchart TD
    UserQuery[User Input Query\nArabic / English] --> API[FastAPI Ingress /api/query/stream]
    API --> ExactCacheCheck{Redis Exact Cache\nSHA256 Hit?}
    
    ExactCacheCheck -- YES --> ReturnExact[Stream Cached Verified Response]
    ExactCacheCheck -- NO --> SemanticCacheCheck{Qdrant Semantic Cache\nE5-Large Cosine Sim >= 0.96?}
    
    SemanticCacheCheck -- YES --> ReturnSemantic[Stream Cached Verified Response]
    SemanticCacheCheck -- NO --> Node1["Node 1: Planner Agent\n(Intent & Language Script Count Lock)"]
    
    Node1 -->|needs_query_expansion = True| Node2["Node 2: Query Rewriter Agent\n(Pronoun Resolution & Multi-Query Decomposition)"]
    Node1 -->|needs_query_expansion = False| Node3["Node 3: Qdrant Hybrid Retriever\n(Dense E5-Large Top-75 + Exact Metadata Top-50)"]
    
    Node2 --> Node3
    Node3 --> Node4["Node 4: Candidate Grouper Engine\n(Sub-Window Merging & SHA256 Fast Deduplication)"]
    Node4 --> Node5["Node 5: Reranker Agent\n(Citation Shield Protocol & Evidence Role Classification)"]
    Node5 --> Node6["Node 6: Relevance Checker Engine v3.1\n(4-Tier Sufficiency Audit: COMPLETE / PARTIAL / INSUFFICIENT)"]
    
    Node6 -- INSUFFICIENT --> Refusal[Emit Controlled Legal Refusal Response]
    Node6 -- SUFFICIENT --> Node7["Node 7: Generator Engine v1.1\n(5 Sub-Engines, EvidenceReasoningGraph, [CLAIM:node_id] Binding)"]
    
    Node7 --> Node8["Node 8: Verification Engine v1.0\n(7-Gate Audit Guardrail & Micro-Repair Engine)"]
    
    Node8 -- PASS / REPAIRED / WARNING --> Node9["Node 9: Response Composer Engine v1.0\n(Zero-LLM Presentation Engine & Multi-Channel Formatting)"]
    Node8 -- FAIL --> Refusal
    
    Node9 --> Node10["Node 10: Certification Authority & Delivery Engine\n(Zero-LLM SHA256 Checksum Proof & CertifiedResponse v1.0)"]
    Node10 --> SaveCache[Save Verified Answer to Redis & Qdrant Cache]
    SaveCache --> StreamResponse[Deliver Certified Payload (Markdown / Streaming / API / Cards)]
```
