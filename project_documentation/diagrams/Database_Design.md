# Database Design: Relational Schemas & Vector Payloads

## 1. PostgreSQL Relational Schema (`db/models.py`)

### ER Diagram

```mermaid
erDiagram
    users ||--o{ uploaded_documents : owns
    users ||--o{ chat_threads : owns
    chat_threads ||--o{ chat_messages : contains
    chat_threads ||--o{ document_jobs : triggers
    document_families ||--o{ documents : groups
    documents ||--o{ document_relationships : relates

    users {
        string id PK "default_tenant"
        string email
        string hashed_password
        datetime created_at
    }

    uploaded_documents {
        string id PK
        string tenant_id FK
        string filename
        int file_size_bytes
        string s3_key
        datetime upload_date
    }

    chat_threads {
        string id PK
        string tenant_id FK
        string title
        datetime created_at
        datetime updated_at
    }

    chat_messages {
        string id PK
        string thread_id FK
        string role "user | assistant"
        text content
        json citations
        datetime timestamp
    }

    document_jobs {
        string id PK
        string tenant_id FK
        string thread_id FK
        string filename
        string status "PENDING | PROCESSING | COMPLETED | FAILED"
        string error_message
        datetime created_at
    }

    document_families {
        string id PK
        string primary_law_number
        string primary_law_year
        string title_ar
        string title_en
    }

    documents {
        string id PK
        string family_id FK
        string law_number
        string law_year
        string article_number
        string article_key "471_1995_78"
        text clean_text
        string parent_chunk_id
    }

    document_relationships {
        string id PK
        string source_doc_id FK
        string target_doc_id FK
        string relationship_type "AMENDS | SUPERSEDES | EXECUTES"
    }
```

---

## 2. Qdrant Vector Collection Payload Schema (`ragnr_documents`)

Each point in the Qdrant `ragnr_documents` collection contains dual vector embeddings and structured payload attributes:

```json
{
  "id": "e8c6f36d-eefb-4a66-b126-5888ae0dcb52",
  "vector": {
    "text-dense": [0.0124, -0.0452, 0.0891, "... (1024 float dimensions)"],
    "text-sparse": {
      "indices": [1024, 4096, 8192, 12288],
      "values": [0.85, 1.42, 0.63, 1.15]
    }
  },
  "payload": {
    "text": "( الماده 78 ) تسري الاحكام السابقه علي منتسبي المدارس والكليات الخاصه من المسجونين بعد موافقه مدير المنشاه العقابيه .",
    "tenant_id": "default_tenant",
    "thread_id": "8dcde63c-1745-4464-b0ec-c3547d61de12",
    "source": "قرار وزاري رقم (471) لسنة 1995م بإصدار اللائحة التنفيذية بالقانون الاتحادي رقم (43) لسنة 1992م في شأن تنظيم المنشآت العقابية.pdf",
    "article": "78",
    "law_number": "471",
    "law_year": "1995",
    "article_key": "471_1995_78",
    "parent_chunk_id": null
  }
}
```

---

## 3. Redis & Qdrant Cache Schemas

### Redis Exact Cache Key Format
- `exact_cache:{tenant_id}:{thread_id}:v{version}:{SHA256(query)}`
- TTL: 86,400 seconds (24 Hours).

### Qdrant Semantic Cache Vector Payload
- Collection Name: `semantic_cache`
- Vector: 1024d Dense BGE-M3 Query Embedding.
- Similarity Match Threshold: $\ge 0.96$.
