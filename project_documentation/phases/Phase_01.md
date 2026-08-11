# Phase 01 — Document Ingestion Engine (Node 0)

## 1. Background
The initial phase of Nomos AI addressed the challenge of ingesting heterogeneous, legacy UAE legal codices published as scanned or digital PDF files containing complex Arabic typography, Kashida diacritics, and structural statutory numbering.

---

## 2. Goals
- Build a resilient PDF text extraction pipeline supporting both digital text streams and scanned image OCR.
- Execute a **10-pass structural Arabic text normalization pipeline** (Kashida stripping, Alef/Ta-Marbuta unification, ASCII digit conversion, control char purging).
- Implement layout-aware structural chunking based on statutory article boundaries (`المادة`) combined with 500-token sub-windowing (100-token overlap).

---

## 3. Architecture Node Mapping
- **Node Number**: **Node 0** ([`Document_Ingestion.md`](file:///d:/RagnrAI/project_documentation/architecture/Document_Ingestion.md))
- **Primary Code Location**: `document_processor/processor.py` & `document_processor/chunker.py`
- **Output Storage**: PostgreSQL relational tables (`documents`, `uploaded_documents`) and Qdrant collection `ragnr_documents` (`dense`: 1024d `multilingual-e5-large` vectors).

---

## 4. Complete Implementation & 10-Pass Normalization

### Key Classes & Modules
- `document_processor.pdf_parser.PDFParser`: Layout parsing via PyMuPDF/Docling with EasyOCR fallback for scanned pages.
- `document_processor.file_handler.TextNormalizer`: Executes 10-pass Arabic normalization.
- `document_processor.chunker.StructuralArticleChunker`: Segments text into canonical article chunks (`article_key = "471_1995_78"`).

```python
class TextNormalizer:
    @staticmethod
    def normalize(text: str) -> str:
        text = re.sub(r'[\u064B-\u0652]', '', text)  # 1. Remove Tashkeel
        text = re.sub(r'ـ', '', text)                # 2. Remove Kashida
        text = re.sub(r'[أإآ]', 'ا', text)           # 3. Unify Alef
        text = re.sub(r'ة', 'ه', text)              # 4. Unify Ta Marbouta
        text = re.sub(r'ى', 'ي', text)              # 5. Unify Alef Maksura
        return re.sub(r'\s+', ' ', text).strip()
```

---

## 5. Integration With Master Architecture
Feeds clean structural article chunks directly into **Node 3** (Qdrant Hybrid Retriever) and **Node 4** (Candidate Grouper Engine).
