# Phase 01 — Corpus Audit, Clean Text Extraction & Preprocessing

## 1. Background
The initial phase of RagnrAI addressed the challenge of ingesting heterogeneous, legacy UAE legal codices published as scanned or digital PDF files containing complex Arabic typography, Kashida diacritics, and structural statutory numbering.

---

## 2. Goals
- Build a resilient PDF text extraction pipeline supporting both digital text streams and scanned image OCR.
- Normalize Arabic Unicode text (stripping Kashidas `ـ`, unifying `أ/إ/آ` to `ا`, and unifying `ة` to `ه`).
- Implement layout-aware structural chunking based on statutory article boundaries (`المادة`) rather than arbitrary token boundaries.

---

## 3. Original Design
The initial plan proposed using standard Python `PyPDF2` text extraction with fixed 500-token sliding window chunking and 50-token overlaps.

---

## 4. Final Production Design
Production empirical testing revealed that sliding window chunking severed legal articles mid-sentence, causing retrieval to fetch disjointed clauses. The final production design uses **Docling** and **PyMuPDF** for layout parsing combined with a **Regex Structural Article Chunker** (`document_processor/chunker.py`) that isolates complete articles into discrete canonical units.

---

## 5. Complete Implementation

### Key Classes & Modules
- `document_processor.pdf_parser.PDFParser`: Layout parsing via PyMuPDF/Docling with EasyOCR fallback for scanned pages.
- `document_processor.file_handler.TextNormalizer`: Performs character unification, Kashida stripping, and whitespace collapse.
- `document_processor.chunker.StructuralArticleChunker`: Uses regex patterns to split documents along article boundaries (`المادة (\d+)`).

```python
class TextNormalizer:
    @staticmethod
    def normalize(text: str) -> str:
        text = re.sub(r'[\u064B-\u0652]', '', text)  # Remove Arabic Tashkeel
        text = re.sub(r'ـ', '', text)                # Remove Kashida
        text = re.sub(r'[أإآ]', 'ا', text)           # Unify Alef
        text = re.sub(r'ة', 'ه', text)              # Unify Ta Marbouta
        return re.sub(r'\s+', ' ', text).strip()
```

---

## 6. Internal Data Flow
```
Raw PDF File
    │
    ▼
Layout-Aware Parsing (Docling / PyMuPDF)
    │
    ▼
Text Normalization (Tashkeel & Kashida Stripping)
    │
    ▼
Regex Statutory Article Boundary Segmentation
    │
    ▼
Hierarchical Metadata Attachment (Law, Year, Article, Key)
```

---

## 7. Inputs
- File: PDF Binary Buffer (`.pdf`) or Plain Text (`.txt`).
- Options: `ocr_enabled: bool`, `tenant_id: str`.

---

## 8. Outputs
- List of clean `Document` objects:
  ```python
  Document(
      page_content="المادة (4) تكون المنشآت العقابية ثلاثة أنواع...",
      metadata={
          "article": "4",
          "law_number": "471",
          "law_year": "1995",
          "article_key": "471_1995_4",
          "source": "قرار وزاري رقم 471 لسنة 1995.pdf"
      }
  )
  ```

---

## 9. Edge Cases
- **Scanned Image PDFs**: Automatically detected via text-to-page bounding box density ratio ($< 0.05$). Triggers OCR engine fallback.
- **Missing Law Year**: Falls back to extracting year from filename or setting `law_year: "UNDATED"`.
- **Multi-Line Article Titles**: Regex pattern matches across newlines to prevent truncated article headings.

---

## 10. Performance Optimizations
- **In-Memory Normalization**: Compiled regular expressions execute normalization in $< 2\text{ ms}$ per page.
- **Page Parallelism**: Multi-page PDF parsing runs in parallel thread pools.

---

## 11. Integration With Other Phases
- Feeds clean structural article chunks directly to **Phase 02 (Vector Indexing & Hybrid Schema Design)**.

---

## 12. Evolution
- *Phase 1*: Fixed sliding window token chunking (500 tokens).
- *Phase 1a*: Transitioned to Regex Structural Boundary Splitting based on empirical evidence showing a +24% boost in recall.

---

## 13. Final State
Production module `document_processor/chunker.py` and `document_processor/file_handler.py` remain active and frozen.
