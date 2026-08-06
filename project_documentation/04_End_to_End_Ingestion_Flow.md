# RagnrAI End-to-End Document Ingestion Flow

## 1. Ingestion Subsystem Overview

The document ingestion subsystem ingests raw legal codices (PDF documents, scanned decrees, executive regulations) and converts them into structured statutory chunks indexed in **Qdrant** (`ragnr_documents`) and **PostgreSQL**.

```
[Raw PDF Document]
       │
       ▼
┌────────────────────────────────────────────────────────┐
│             File Ingress & Preprocessing               │
│  - S3 / Local Storage Persistence                      │
│  - Document Job Registration (Status: PENDING)         │
│  - Unicode & Arabic Kashida Normalization              │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│           Layout-Aware Extraction Engine               │
│  - Docling / PyMuPDF Layout Analysis                   │
│  - OCR Fallback for Scanned Documents                  │
│  - Table Extraction & Markdown Conversion              │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│       Structural Statutory Boundary Chunker            │
│  - Regex Parsing: (المادة / Article / Chapter)         │
│  - Statutory Hierarchy Preservation (Law->Art->Clause) │
│  - Parent Chunk & Sub-Split Linkage                    │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│         Dual Embedding & Indexing Pipeline             │
│  - BGE-M3 Dense Embedding (1024 Dimensions)            │
│  - Qdrant Sparse BM25 Token Weight Computation         │
│  - PostgreSQL Metadata Record Creation                 │
│  - Qdrant Vector Collection Upsert (`ragnr_documents`) │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│             Tenant Cache Invalidations                 │
│  - Tenant Version Increment in Redis                   │
│  - Ingestion Job Status Update (COMPLETED)             │
└────────────────────────────────────────────────────────┘
```

---

## 2. Detailed Ingestion Pipeline Stages

### Stage 1: Document Upload & Storage Ingress
- **Endpoint**: `POST /api/upload`
- **Inputs**: `file: UploadFile`, `tenant_id: str`, `thread_id: Optional[str]`
- **Actions**:
  1. Writes file to local storage or S3 bucket (`s3_client.upload_fileobj`).
  2. Inserts record in PostgreSQL `document_jobs` table with `status="PENDING"`.
  3. Dispatches async Celery task (`process_document_task`).

---

### Stage 2: Layout-Aware Parsing & Extraction
- **Module**: `document_processor/pdf_parser.py` & `file_handler.py`
- **Operations**:
  1. Utilizes **Docling** or **PyMuPDF** to extract text while maintaining spatial reading order.
  2. Applies OCR engine (Tesseract/EasyOCR) if page image density indicates scanned PDF.
  3. Strips Arabic Kashida diacritics (`ـ`), unifies Unicode characters (`أ/إ/آ` $\rightarrow$ `ا`, `ة` $\rightarrow$ `ه`).
  4. Preserves tables in clean Markdown format.

---

### Stage 3: Structural Statutory Boundary Chunking
- **Module**: `document_processor/chunker.py`
- **Core Algorithm**: **Regex Structural Article Splitting** (rather than arbitrary token counts).
- **Matching Rules**:
  ```python
  ARTICLE_REGEX = r'(?:المادة|المادّة|الماده|Article)\s*\(?\s*(\d+|\b[أ-ي]+\b)\s*\)?'
  LAW_REGEX = r'(?:قانون|قرار|مرسوم)\s+(?:اتحادي|وزاري)?\s*رقم\s*\(?\s*(\d+)\s*\)?\s*لسنة\s*(\d{4})'
  ```
- **Hierarchical Chunk Metadata**:
  - `article`: Article number (e.g. `"78"`)
  - `law_number`: Law number (e.g. `"471"`)
  - `law_year`: Law year (e.g. `"1995"`)
  - `article_key`: Unique canonical key (e.g. `"471_1995_78"`)
  - `parent_chunk_id`: ID of full parent article chunk (for sub-split sub-articles).

---

### Stage 4: Dual Embedding Computation
- **Model**: `BAAI/bge-m3`
- **Dense Vector**: 1024-dimensional floating point array representing deep semantic intent.
- **Sparse Vector**: Token-level BM25 TF-IDF weight dictionary mapping term IDs to importance scores:
  ```python
  sparse_vector = {
    "indices": [1024, 4096, 8192, ...],
    "values": [0.85, 1.42, 0.63, ...]
  }
  ```

---

### Stage 5: Dual Storage Upsert

#### A. PostgreSQL Relational Persistence (`db/models.py`)
Creates or updates records across:
- `uploaded_documents`: Records file name, byte size, S3 URL, upload timestamp.
- `documents`: Stores statutory chunk text, article key, law number, and parent links.
- `document_families`: Groups related executive regulations under primary law codices.

#### B. Qdrant Vector Collection Upsert (`db/qdrant_client.py`)
Upserts points to collection `ragnr_documents`:
```python
PointStruct(
    id=str(uuid.uuid4()),
    vector={
        "text-dense": dense_vector_1024d,
        "text-sparse": sparse_vector_bm25
    },
    payload={
        "text": clean_statutory_text,
        "tenant_id": tenant_id,          # e.g. "default_tenant"
        "thread_id": thread_id,          # e.g. "8dcde63c-..." or None for global
        "source": pdf_filename,
        "article": article_num,
        "law_number": law_num,
        "law_year": law_year,
        "article_key": article_key,
        "parent_chunk_id": parent_id
    }
)
```

---

### Stage 6: Cache Invalidation & Telemetry Update
1. Increments Redis tenant version counter (`tenant_version:{tenant_id}`).
2. Invalidates exact query cache entries associated with the updated tenant scope.
3. Updates `document_jobs` status in PostgreSQL to `"COMPLETED"`.
