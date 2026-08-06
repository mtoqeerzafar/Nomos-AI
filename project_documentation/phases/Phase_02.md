# Phase 02 — Vector Indexing & Hybrid Schema Design

## 1. Background
To enable rapid, multi-tenant hybrid retrieval over statutory chunks, Phase 02 established the vector database schemas in **Qdrant** (`ragnr_documents`) and relational metadata schemas in **PostgreSQL**.

---

## 2. Goals
- Configure Qdrant collection `ragnr_documents` to support dual vectors (Dense 1024d + Sparse BM25).
- Define PostgreSQL ORM models (`db/models.py`) for tenant users, chat threads, messages, background jobs, and statutory document relationships.
- Establish strict multi-tenant payload indexing rules for instant filtered vector queries.

---

## 3. Original Design
Single dense vector index using OpenAI `text-embedding-ada-002` (1536d) in Qdrant with no sparse BM25 index or relational metadata linking.

---

## 4. Final Production Design
Replaced OpenAI embeddings with **BGE-M3** (`BAAI/bge-m3`), an open multilingual model generating 1024-dimensional dense vectors alongside native sparse BM25 term weights. Configured Qdrant payload schema with indexed keyword fields for `tenant_id`, `thread_id`, `law_number`, `law_year`, and `article_key`.

---

## 5. Complete Implementation

### Qdrant Collection Configuration (`db/qdrant_client.py`)
```python
client.create_collection(
    collection_name="ragnr_documents",
    vectors_config={
        "text-dense": VectorParams(size=1024, distance=Distance.COSINE)
    },
    sparse_vectors_config={
        "text-sparse": SparseVectorParams(
            index=SparseIndexParams(on_disk=False)
        )
    }
)
```

### Payload Index Creation
```python
payload_fields = ["tenant_id", "thread_id", "law_number", "law_year", "article_key", "source"]
for field in payload_fields:
    client.create_payload_index(
        collection_name="ragnr_documents",
        field_name=field,
        field_schema=PayloadSchemaType.KEYWORD
    )
```

---

## 6. Internal Data Flow
```
Clean Statutory Chunks (from Phase 01)
    │
    ├──────────> BGE-M3 Dense Encoder (1024d Vector)
    │
    ├──────────> BGE-M3 Sparse Encoder (BM25 Term Weights)
    │
    ▼
Qdrant PointStruct Creation (Payload + Dual Vectors)
    │
    ▼
Batch Upsert to Qdrant `ragnr_documents`
```

---

## 7. Inputs
- Structured `Document` objects with metadata payloads.
- Vector configuration parameters (1024d, Cosine distance).

---

## 8. Outputs
- Upserted Qdrant points with UUID keys.
- Indexed payload fields in Qdrant memory.

---

## 9. Edge Cases
- **Null `thread_id`**: Global pre-indexed legal codices set `thread_id: None` (or omitted). The retrieval engine filters for `thread_id == active_thread OR thread_id IS NULL`.
- **Large Batches**: Upserts are chunked in batches of 64 points to prevent gRPC payload size limit breaches.

---

## 10. Performance Optimizations
- **On-Disk Payload Storage**: Large statutory text payloads are kept on disk while vector indices remain in RAM for $< 10\text{ ms}$ search speed.
- **FastEmbed Preloading**: Embedding models are loaded into system RAM during server lifespan startup.

---

## 11. Integration With Other Phases
- Provides vector and payload infrastructure consumed by **Phase 03 (Hybrid Retrieval)**.

---

## 12. Evolution
- Transitioned from single vector configuration to dual dense/sparse vector configuration.
- Added payload field indices to eliminate unindexed full-collection scans.

---

## 13. Final State
Active in `db/qdrant_client.py` and `db/models.py`. Collection `ragnr_documents` is production-frozen.
