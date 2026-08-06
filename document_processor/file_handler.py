import os
import re
import json
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import List
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from config import constants
from config.settings import settings
from utils.logging import logger

class StructureAwareChunker:
    def __init__(self, source_name: str):
        self.source_name = source_name
        self.paragraph_batch_size = settings.PARAGRAPH_BATCH_SIZE
        self.max_table_rows_per_chunk = settings.MAX_TABLE_ROWS_PER_CHUNK
        self.table_row_batch_size = settings.TABLE_ROW_BATCH_SIZE

    def chunk_document(self, docling_doc) -> List[Document]:
        chunks = []
        current_block = []
        last_flushed_paragraph = ""
        current_page_no = 1

        current_meta = {
            "h1": None,
            "h2": None,
            "h3": None,
            "h4": None
        }

        def flush_paragraphs():
            nonlocal current_block, last_flushed_paragraph, current_page_no
            if current_block:
                para_text = "\n".join(current_block)
                chunks.append(self._build_chunk(para_text, current_meta.copy(), "paragraph", current_page_no))
                last_flushed_paragraph = para_text
                current_block = []

        for item, level in docling_doc.iterate_items():
            if hasattr(item, "prov") and item.prov:
                current_page_no = getattr(item.prov[0], "page_no", current_page_no)
            
            # 1. Heading Management
            if item.label in ("section_header", "page_header", "title"):
                flush_paragraphs()
                last_flushed_paragraph = "" # Reset context on new heading
                text = item.text.strip()
                if level == 1:
                    current_meta["h1"] = text
                    current_meta["h2"] = None
                    current_meta["h3"] = None
                    current_meta["h4"] = None
                elif level == 2:
                    current_meta["h2"] = text
                    current_meta["h3"] = None
                    current_meta["h4"] = None
                elif level == 3:
                    current_meta["h3"] = text
                    current_meta["h4"] = None
                else:
                    current_meta["h4"] = text
                continue
                
            # 3. & 7. Table Processing & Defensive Handling
            if item.label == "table":
                flush_paragraphs()
                try:
                    table_md = item.export_to_markdown()
                    rows = table_md.strip().split('\n')
                    context_prefix = f"{last_flushed_paragraph}\n\n" if last_flushed_paragraph else ""
                    
                    if len(rows) <= self.max_table_rows_per_chunk:
                        chunks.append(self._build_chunk(context_prefix + table_md, current_meta.copy(), "table", getattr(item.prov[0], "page_no", None) if item.prov else None))
                    else:
                        header = rows[0:2]
                        body = rows[2:]
                        for i in range(0, len(body), self.table_row_batch_size):
                            batch = body[i:i + self.table_row_batch_size]
                            batch_md = "\n".join(header + batch)
                            chunks.append(self._build_chunk(context_prefix + batch_md, current_meta.copy(), "table", getattr(item.prov[0], "page_no", None) if item.prov else None))
                except Exception as e:
                    logger.warning(f"Failed to extract table structure, falling back to text: {e}")
                    context_prefix = f"{last_flushed_paragraph}\n\n" if last_flushed_paragraph else ""
                    chunks.append(self._build_chunk(context_prefix + item.text, current_meta.copy(), "table_fallback", getattr(item.prov[0], "page_no", None) if item.prov else None))
                continue

            # 5. List Processing
            if item.label == "list_item":
                flush_paragraphs()
                context_prefix = f"{last_flushed_paragraph}\n\n" if last_flushed_paragraph else ""
                chunks.append(self._build_chunk(context_prefix + item.text, current_meta.copy(), "list", getattr(item.prov[0], "page_no", None) if item.prov else None))
                continue

            # 4. & 6. Paragraph Processing & Unknown Element Fallback
            if hasattr(item, "text") and item.text:
                text = item.text.strip()
                if text:
                    # Check if paragraph contains an explicit Article pattern (e.g. المادة 15 or المادة (15))
                    # OCR produces two common article heading formats:
                    #   Standard:        المادة 9  /  مادة (9)
                    #   Inverted-paren:  ( الماده )9   <- frequent in EasyOCR output
                    art_match = re.search(
                        r'\(\s*(?:المادة|الماده|مادة|ماده)\s*\)\s*\d+'
                        r'|(?:المادة|الماده|مادة|ماده)\s*\(?\s*\d+\s*\)?',
                        text
                    )
                    if art_match:
                        raw_h1 = art_match.group(0)
                        num_m = re.search(r'\d+', raw_h1)
                        if num_m:
                            current_meta["h1"] = f"المادة {num_m.group(0)}"
                        else:
                            current_meta["h1"] = raw_h1
                    current_block.append(text)
                
                if len(current_block) >= self.paragraph_batch_size:
                    flush_paragraphs()

        flush_paragraphs()
        return chunks

    def _build_chunk(self, text: str, meta: dict, chunk_type: str, page_no: int = None) -> Document:
        # Minimal, machine-friendly structural header formatting (Phase B3A)
        header_lines = [f"LAW: {self.source_name}"]
        
        article_num = None
        section_path = []
        for k in ["h1", "h2", "h3", "h4"]:
            val = meta.get(k)
            if val:
                if "ماد" in val.lower() or "مادة" in val or "المادة" in val:
                    article_num = val
                else:
                    section_path.append(val)
                    
        if article_num:
            header_lines.append(f"ARTICLE: {article_num}")
        if section_path:
            header_lines.append(f"SECTION: {' > '.join(section_path)}")
            
        header_block = "\n".join(header_lines)
        enriched_text = f"{header_block}\n--------------\n{text}"
        
        metadata = {
            "source": self.source_name,
            "file_name": self.source_name,
            "chunk_type": chunk_type,
            "hierarchy": meta,
            "index_version": "structured_headers_v1",
            "embedding_model": "intfloat/multilingual-e5-large",
            "chunk_schema": "v2"
        }
        if page_no is not None:
            metadata["page"] = page_no
            
        return Document(
            page_content=enriched_text,
            metadata=metadata
        )

class DocumentProcessor:
    def __init__(self):
        self.cache_dir = Path(settings.CACHE_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.converter = None # Lazy loaded

    def _get_converter(self):
        if self.converter is None:
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import (
                PdfPipelineOptions, EasyOcrOptions, TesseractOcrOptions,
                AcceleratorOptions, AcceleratorDevice
            )
            
            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = True # Enable OCR for scanned Arabic documents
            pipeline_options.generate_page_images = False
            pipeline_options.accelerator_options = AcceleratorOptions(
                num_threads=2, device=AcceleratorDevice.CPU
            )
            
            # Configure EasyOCR (ar/en) with fallback to Tesseract (ara+eng)
            try:
                pipeline_options.ocr_options = EasyOcrOptions(lang=["ar", "en"])
            except Exception:
                try:
                    pipeline_options.ocr_options = TesseractOcrOptions(lang="ara+eng")
                except Exception as ocr_err:
                    logger.warning(f"Could not configure custom OCR options: {ocr_err}")
            
            self.converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                }
            )
        return self.converter

    def _is_flat_spreadsheet(self, file_path: Path) -> bool:
        import pandas as pd
        try:
            suffix = file_path.suffix.lower()
            if suffix == '.csv':
                df = pd.read_csv(file_path, nrows=5)
                return len(df.columns) > 0
                
            xl = pd.ExcelFile(file_path)
            if len(xl.sheet_names) > 1:
                return False # Multiple sheets -> complex
                
            df = pd.read_excel(file_path, sheet_name=0, nrows=10)
            if df.columns.to_series().astype(str).str.contains("Unnamed").mean() > 0.4:
                return False # Too many unnamed columns -> complex layout
            return True
        except Exception:
            return False

    def _parse_flat_spreadsheet(self, file_path: Path) -> List[Document]:
        import pandas as pd
        chunks = []
        try:
            if file_path.suffix.lower() == '.csv':
                df = pd.read_csv(file_path)
            else:
                df = pd.read_excel(file_path)
                
            df = df.fillna("")
            
            for index, row in df.iterrows():
                row_items = []
                for col in df.columns:
                    val = str(row[col]).strip()
                    if val:
                        row_items.append(f"{col}: {val}")
                row_text = "\n".join(row_items)
                
                metadata = {
                    "source": file_path.name,
                    "file_name": file_path.name,
                    "chunk_type": "spreadsheet_row",
                    "row_index": index
                }
                
                breadcrumb = f"[Source: {file_path.stem} > Row {index + 1}]"
                enriched_text = f"{breadcrumb}\n\n{row_text}"
                
                chunks.append(Document(page_content=enriched_text, metadata=metadata))
        except Exception as e:
            logger.error(f"Failed to parse flat spreadsheet {file_path.name}: {e}")
        return chunks

    def process_single_file(self, file_path: Path) -> List[Document]:
        """Process a single file, typically downloaded from S3 by a Celery worker."""
        file_ext = file_path.suffix.lower()
        if file_ext not in ('.pdf', '.docx', '.txt', '.md', '.xlsx', '.csv'):
            logger.warning(f"Skipping unsupported file type: {file_path}")
            return []

        try:
            # Generate content-based hash for caching
            with open(file_path, "rb") as f:
                file_hash = self._generate_hash(f.read())
            
            cache_path = self.cache_dir / f"{file_hash}.json"
            
            if self._is_cache_valid(cache_path):
                logger.info(f"Loading from cache: {file_path.name}")
                chunks = self._load_from_cache(cache_path, file_name=file_path.name)
            else:
                logger.info(f"Processing and caching: {file_path.name}")
                
                if file_ext in ('.xlsx', '.csv') and self._is_flat_spreadsheet(file_path):
                    logger.info(f"Routing flat spreadsheet to Pandas Row Parser: {file_path.name}")
                    chunks = self._parse_flat_spreadsheet(file_path)
                else:
                    # Copy file to a temporary ASCII-safe path to prevent docling-parse v2 failures on Windows
                    import tempfile
                    import shutil
                    
                    temp_dir = Path(tempfile.gettempdir())
                    temp_file_path = temp_dir / f"docling_temp_{file_hash}{file_ext}"
                    try:
                        shutil.copy(file_path, temp_file_path)
                        # Extract Docling DOM
                        converter = self._get_converter()
                        docling_doc = converter.convert(temp_file_path).document
                        
                        # Chunk using Custom Structure-Aware Chunker
                        chunker = StructureAwareChunker(file_path.stem)
                        chunks = chunker.chunk_document(docling_doc)
                    finally:
                        if temp_file_path.exists():
                            try:
                                temp_file_path.unlink()
                            except Exception as cleanup_err:
                                logger.warning(f"Failed to delete temp file {temp_file_path}: {cleanup_err}")
                
                # Sanitize Enterprise PII/PHI/Secrets and normalize Arabic text
                from document_processor.normalization import normalize_arabic_text, normalize_numerals
                try:
                    from security.guardrails.pii_guardrail import pii_guardrail
                    for chunk in chunks:
                        chunk.page_content = pii_guardrail.sanitize(chunk.page_content)
                        chunk.page_content = normalize_numerals(normalize_arabic_text(chunk.page_content))
                except ImportError:
                    # security package not installed — skip PII scrubbing, just normalize Arabic
                    logger.debug("security.guardrails not available — skipping PII sanitization (Arabic normalization still applied)")
                    for chunk in chunks:
                        chunk.page_content = normalize_numerals(normalize_arabic_text(chunk.page_content))
                except Exception as e:
                    logger.error(f"Failed to sanitize/normalize {file_path.name}: {e}")


                self._save_to_cache(chunks, cache_path)
            
            return chunks
            
        except Exception as e:
            logger.error(f"Failed to process {file_path}: {str(e)}")
            raise e

    def _generate_hash(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _save_to_cache(self, chunks: List[Document], cache_path: Path):
        data = {
            "timestamp": datetime.now().timestamp(),
            "chunks": [{"page_content": c.page_content, "metadata": c.metadata} for c in chunks]
        }
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load_from_cache(self, cache_path: Path, file_name: str = None) -> List[Document]:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        docs = []
        for c in data["chunks"]:
            meta = dict(c["metadata"])
            content = c["page_content"]
            if file_name:
                meta["source"] = file_name
                meta["file_name"] = file_name
                if content.startswith("LAW:"):
                    # Replace only the source name in the LAW: prefix.
                    # IMPORTANT: normalize_arabic_text() strips newlines from page_content before
                    # caching, so the entire chunk may be a single line:
                    #   "LAW: old_stem ARTICLE: ... -------------- body text"
                    # The naive approach of split("\n")[0] would therefore match the ENTIRE content
                    # and replace it with just "LAW: {file_name}", destroying the article body.
                    # Fix: use a regex that stops at the first structural boundary.
                    import re as _re
                    content = _re.sub(
                        r'^LAW:\s*[^\n]*?(?=(?:\n|ARTICLE:|SECTION:|-{3,}|\Z))',
                        f"LAW: {file_name}",
                        content,
                        count=1,
                        flags=_re.DOTALL
                    )
            docs.append(Document(page_content=content, metadata=meta))
        return docs

    def _is_cache_valid(self, cache_path: Path) -> bool:
        if not cache_path.exists():
            return False
            
        cache_age = datetime.now() - datetime.fromtimestamp(cache_path.stat().st_mtime)
        return cache_age < timedelta(days=settings.CACHE_EXPIRE_DAYS)