"""
Production Ingestion Pipeline — Module v1.1
Canonical source of truth for document processing, chunking, v1.1 schema payload building, and Qdrant indexing.
Used by BOTH the Celery background upload task (workers/tasks.py) AND CLI ingestion scripts (scripts/execute_production_ingestion.py).
"""

import re
import unicodedata
import hashlib
import sys
import os
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

import tiktoken
from langchain_core.documents import Document
from qdrant_client.models import FilterSelector, Filter

from config.settings import settings
from db.qdrant_client import qdrant_manager
from document_processor.file_handler import DocumentProcessor
from utils.logging import logger

EMBEDDING_SAFE_LIMIT = 512   # max tokens per indexed vector; exact model max
WINDOW_SIZE          = 500   # tokens per sliding window for oversized articles
WINDOW_OVERLAP       = 100   # token overlap between consecutive windows

try:
    enc = tiktoken.get_encoding("cl100k_base")
    def count_tokens(text: str) -> int:
        return len(enc.encode(text))
except Exception:
    def count_tokens(text: str) -> int:
        return len(text.split())


# ── Text helpers ───────────────────────────────────────────────────────────────

def sanitize_arabic_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'ـ+', '', text)
    text = re.sub(r'\)\s*(\d+)\s*\(', r'(\1)', text)
    text = re.sub(r'\(\s*(\d+)\s*\)', r'(\1)', text)
    text = re.sub(r'\bالمر\s+سوم\b', 'المرسوم', text)
    text = re.sub(r'\bجر\s+يمه\b', 'جريمة', text)
    text = re.sub(r'[\u200B-\u200D\uFEFF\uFFFD]', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()



def extract_cross_references(text: str) -> Tuple[list, list]:
    """Extract law cross-references returning (raw_refs, normalized_keys) tuple."""
    if not text:
        return [], []
    pattern = r'(?:للقانون|بالقانون|القانون|للمرسوم|بالمرسوم|المرسوم بقانون|مرسوم بقانون|المرسوم|مرسوم|للقرار|بالقرار|القرار الوزاري|قرار وزاري|القرار|قرار)\s*(?:الاتحادي|الوزاري)?\s*(?:رقم)?\s*[\(\)]*\s*(\d+)\s*[\(\)]*\s*(?:لسنة|لسنه|سنة|سنه)\s*(\d{4})\s*م?'
    refs = []
    keys = []
    for m in re.finditer(pattern, text):
        ref = m.group(0).strip()
        law_num, law_yr = m.group(1), m.group(2)
        key = f"{law_num}_{law_yr}"
        if ref not in refs:
            refs.append(ref)
        if key not in keys:
            keys.append(key)
    return refs, keys


def sliding_windows(tokens: list, size: int, overlap: int) -> list:
    """Split a token list into overlapping windows; return list of token-lists."""
    step = size - overlap
    windows = []
    i = 0
    while i < len(tokens):
        windows.append(tokens[i : i + size])
        if i + size >= len(tokens):
            break
        i += step
    return windows


def normalize_domain(raw_domain: str | None) -> str:
    if not raw_domain:
        return "Legal"
    d = raw_domain.strip().lower()
    if "مال" in d or "finan" in d:
        return "Finance"
    elif "عقوب" in d or "جزائي" in d or "crim" in d:
        return "Criminal Law"
    elif "مرور" in d or "traff" in d:
        return "Traffic"
    elif "عدل" in d or "justic" in d:
        return "Justice"
    return "Legal"


# ── Article grouping & Header stripping ───────────────────────────────────────

ORDINAL_MAP = {
    'الأولى': '1', 'الثانية': '2', 'الثالثة': '3', 'الرابعة': '4',
    'الخامسة': '5', 'السادسة': '6', 'السابعة': '7', 'الثامنة': '8',
    'التاسعة': '9', 'العاشرة': '10',
    'الاولي': '1', 'الثانيه': '2', 'الثالثه': '3', 'الرابعه': '4',
    'الخامسه': '5', 'السادسه': '6', 'السابعه': '7', 'الثامنه': '8',
    'التاسعه': '9', 'العاشره': '10',
}

# Arabic presentation form normalization
def _nfkc(text: str) -> str:
    """Normalize Arabic presentation-form glyphs (U+FB50-FDFF) to standard Unicode (U+0600-U+06FF).
    This handles OCR output that uses presentation-form Arabic characters.
    """
    return unicodedata.normalize('NFKC', text) if text else text


ARTICLE_HEADING_RE = re.compile(
    # Standard Arabic Unicode + presentation-form fallback (after NFKC normalization)
    r'(?:ARTICLE:\s*)?(?:المادة|الماده|مادة|ماده|مادة|الماده)\s*[\(\)]*\s*(\d+|الأولى|الثانية|الثالثة|الرابعة|الخامسة|السادسة|السابعة|الثامنة|التاسعة|العاشرة|الاولي|الثانيه|الثالثه|الرابعه|الخامسه|السادسه|السابعه|الثامنه|التاسعه|العاشره)\s*[\(\)]*|'
    r'\(\s*(?:المادة|الماده|مادة|ماده)\s*\)\s*(\d+)|'
    r'[\(\"]\s*(\d+)\s*[\)\"]\s*(?:المادة|الماده|مادة|ماده)|'
    r'ARTICLE:\s*(\d+)',
    re.IGNORECASE
)

_HEADER_STRIP_RE = re.compile(
    r'^LAW:.+?-{3,}\s*',
    re.DOTALL
)

def _strip_chunk_header(text: str) -> str:
    """Remove the structured header prefix added by StructureAwareChunker._build_chunk()."""
    return _HEADER_STRIP_RE.sub('', text, count=1).strip()


def _first_group(m) -> str | None:
    if not m:
        return None
    for g in m.groups():
        if g is not None:
            return ORDINAL_MAP.get(g, g)
    return None


def extract_article_num(text: str, meta: dict) -> str | None:
    """Extract article number from text or hierarchy metadata.
    Applies NFKC normalization to handle OCR presentation-form Arabic.
    """
    hierarchy = meta.get("hierarchy", {})
    if isinstance(hierarchy, dict):
        for h in ("h1", "h2", "h3", "h4"):
            hv = hierarchy.get(h, "") or ""
            if hv:
                norm_hv = _nfkc(hv)
                m = ARTICLE_HEADING_RE.search(norm_hv)
                result = _first_group(m)
                if result:
                    return result

    for h in ("h1", "h2", "h3", "h4"):
        hv = meta.get(h, "") or ""
        if hv:
            norm_hv = _nfkc(hv)
            m = ARTICLE_HEADING_RE.search(norm_hv)
            result = _first_group(m)
            if result:
                return result

    # Search full text with normalization
    norm_text = _nfkc(text)
    m = ARTICLE_HEADING_RE.search(norm_text)
    return _first_group(m)


def group_by_article(chunks: list[Document], source_name: str) -> dict:
    grouped: dict[str, dict] = {}
    current_art_key = None
    general_counter = 0

    for doc in chunks:
        # Normalize presentation-form Arabic before article detection
        text = _nfkc(doc.page_content.strip())
        meta = dict(doc.metadata)

        art_found = extract_article_num(text, meta)
        if art_found:
            current_art_key = art_found

        key = current_art_key
        if key is None:
            key = f"GENERAL_{general_counter}"
            general_counter += 1

        if key not in grouped:
            grouped[key] = {"text_parts": [], "base_meta": meta, "source": source_name, "is_general": key.startswith("GENERAL")}
        # Store original (non-normalized) text in text_parts for accurate embedding
        grouped[key]["text_parts"].append(doc.page_content.strip())

    return grouped


# ── Payload Builder ────────────────────────────────────────────────────────────

def build_chunks_for_article(
    source: str,
    art_key: str,
    text_parts: list,
    base_meta: dict,
    article_position: int = 1,
    prev_article_key: str | None = None,
    next_article_key: str | None = None,
    pg_meta: dict | None = None,
) -> list[tuple[str, dict]]:
    """Build production v1.1 payload tuples for an article."""
    law_num_m = re.search(r'رقم\s*\(?\s*(\d+)\s*\)?', source)
    law_yr_m  = re.search(r'لسنة\s*(\d{4})', source)
    law_num   = law_num_m.group(1) if law_num_m else ""
    law_year  = law_yr_m.group(1)  if law_yr_m  else ""

    clean_parts = [_strip_chunk_header(part) for part in text_parts]
    full_body   = "\n\n".join(p for p in clean_parts if p)
    clean_source_title = Path(source).name.replace(".pdf", "").strip()
    header_prefix  = f"LAW: {clean_source_title}\nARTICLE: المادة ({art_key})\n\n"
    full_text      = sanitize_arabic_text(header_prefix + full_body)
    xrefs, xref_keys = extract_cross_references(full_body)
    parent_id        = f"parent_{hashlib.md5((source + art_key).encode()).hexdigest()[:12]}"
    doc_hash         = hashlib.md5((source + art_key).encode('utf-8')).hexdigest()

    tokens = enc.encode(full_text)
    tok_count = len(tokens)

    clean_title = clean_source_title
    article_title = f"المادة {art_key}"
    article_canonical_key = f"{law_num}_{law_year}_{art_key}" if (law_num and law_year) else f"art_{art_key}"
    
    prev_canonical_art_key = f"{law_num}_{law_year}_{prev_article_key}" if (law_num and law_year and prev_article_key) else (f"art_{prev_article_key}" if prev_article_key else None)
    next_canonical_art_key = f"{law_num}_{law_year}_{next_article_key}" if (law_num and law_year and next_article_key) else (f"art_{next_article_key}" if next_article_key else None)

    if tok_count <= EMBEDDING_SAFE_LIMIT:
        window_texts = [full_text]
    else:
        windows = sliding_windows(tokens, WINDOW_SIZE, WINDOW_OVERLAP)
        window_texts = [sanitize_arabic_text(enc.decode(w)) for w in windows]

    total_chunks = len(window_texts)
    chunk_ids = [f"chunk_{hashlib.md5(wt.encode()).hexdigest()[:12]}" for wt in window_texts]

    results = []
    step_size = WINDOW_SIZE - WINDOW_OVERLAP
    for idx, win_text in enumerate(window_texts):
        cid = chunk_ids[idx]
        is_parent = (total_chunks > 1 and idx == 0)
        children = chunk_ids[1:] if is_parent else []

        prev_cid = chunk_ids[idx - 1] if idx > 0 else None
        next_cid = chunk_ids[idx + 1] if idx < total_chunks - 1 else None

        win_tok_count = count_tokens(win_text)
        w_start = (idx * step_size) if total_chunks > 1 else 0
        w_end = (w_start + win_tok_count) if total_chunks > 1 else tok_count

        m = dict(base_meta)
        m.pop("document", None)
        
        hierarchy = dict(m.get("hierarchy", {}))
        hierarchy["h1"] = article_title
        hierarchy["h2"] = None
        hierarchy["h3"] = None
        hierarchy["h4"] = None

        payload = {
            "source":               Path(source).name,
            "title":                clean_title,
            "article_title":        article_title,
            "article_key":          article_canonical_key,
            "article":              art_key,
            "article_position":     article_position,
            "previous_article_key": prev_canonical_art_key,
            "next_article_key":     next_canonical_art_key,
            "page":                 base_meta.get("page", base_meta.get("page_no", 1)),
            "jurisdiction":         "UAE",
            "language":             "ar",
            "document_type":        "Federal Law",
            "section_type":         "ARTICLE",
            "hierarchy":            hierarchy,
            "chunk_index":          idx,
            "total_chunks":         total_chunks,
            "window_number":        idx + 1,
            "window_start_token":   w_start,
            "window_end_token":     w_end,
            "token_count":          win_tok_count,
            "is_first_window":      (idx == 0),
            "is_last_window":       (idx == total_chunks - 1),
            "previous_chunk_id":    prev_cid,
            "next_chunk_id":        next_cid,
            "article_number":       art_key,
            "law_number":           law_num,
            "law_year":             law_year,
            "canonical_citation":   f"{clean_title} {article_title}",
            "hierarchy_path":       [clean_title, article_title],
            "hierarchy_depth":      2,
            "text":                 win_text,
            "chunk_id":             cid,
            "parent_chunk_id":      parent_id if total_chunks > 1 else None,
            "is_parent":            is_parent,
            "child_chunk_ids":      children,
            "chunk_level":          "ARTICLE",
            "character_count":      len(win_text),
            "embedding_version":    "v2.1_multilingual_e5_v1.1",
            "doc_hash":             doc_hash,
            "cross_references":     xrefs,
            "cross_reference_keys": xref_keys,
            "lifecycle_status":     "Active",
            "tenant_id":            "default_tenant",
            "ingestion_spec":       "v1.1",
        }
        if pg_meta:
            payload.update(pg_meta)

        results.append((win_text, payload))

    return results


def process_and_index_document(
    file_path: Path | str,
    document_id: str | None = None,
    tenant_id: str = "default_tenant",
    thread_id: str | None = None,
    s3_key: str | None = None,
    family_domain: str | None = None,
    lifecycle_status: str = "Active",
    effective_date_gregorian: str | None = None,
    expiry_date_gregorian: str | None = None,
    allowed_roles: list | None = None,
    collection_name: str | None = None
) -> Tuple[int, int]:
    """
    Unified entry point for document ingestion.
    Processes a single document file into v1.1 payload chunks and indexes them into Qdrant.
    Returns (indexed_count, quarantined_count).
    """
    file_path = Path(file_path)
    processor = DocumentProcessor()
    raw_chunks = processor.process_single_file(file_path)
    source_name = file_path.name

    logger.info(f"[Pipeline] {source_name}: {len(raw_chunks)} raw chunks from DocumentProcessor")

    grouped = group_by_article(raw_chunks, source_name)
    art_keys = [k for k, v in grouped.items() if not v.get("is_general")]
    general_keys = [k for k, v in grouped.items() if v.get("is_general")]

    logger.info(f"[Pipeline] {source_name}: {len(art_keys)} articles, {len(general_keys)} general/unclassified groups")

    # FALLBACK: If no articles detected (e.g. non-article-structured doc), treat all chunks
    # as flat document-level chunks rather than silently quarantining everything.
    if not art_keys and grouped:
        logger.warning(
            f"[Pipeline] {source_name}: No article headings detected. "
            f"Treating all {len(raw_chunks)} chunks as flat document chunks (fallback mode)."
        )
        # Rebuild grouped treating every chunk as its own flat entry
        flat_grouped = {}
        for i, doc in enumerate(raw_chunks):
            flat_key = f"FLAT_{i}"
            flat_grouped[flat_key] = {
                "text_parts": [doc.page_content],
                "base_meta": dict(doc.metadata),
                "source": source_name,
                "is_general": False,
                "is_flat": True,
            }
        grouped = flat_grouped
        art_keys = list(flat_grouped.keys())

    upload_queue = []
    quarantine_count = 0
    seen_ids = set()

    pg_meta = {
        "tenant_id": tenant_id,
        "lifecycle_status": lifecycle_status,
        "domain": normalize_domain(family_domain),
    }
    if document_id:
        pg_meta["document_id"] = document_id
    if thread_id:
        pg_meta["thread_id"] = thread_id
    if s3_key:
        pg_meta["s3_key"] = s3_key
    if effective_date_gregorian:
        pg_meta["effective_date_gregorian"] = effective_date_gregorian
    if expiry_date_gregorian:
        pg_meta["expiry_date_gregorian"] = expiry_date_gregorian
    if allowed_roles:
        pg_meta["allowed_roles"] = allowed_roles

    for art_idx, art_key in enumerate(art_keys):
        data = grouped[art_key]
        art_pos = art_idx + 1
        prev_art_key = art_keys[art_idx - 1] if art_idx > 0 else None
        next_art_key = art_keys[art_idx + 1] if art_idx < len(art_keys) - 1 else None

        built = build_chunks_for_article(
            source_name,
            art_key,
            data["text_parts"],
            data["base_meta"],
            article_position=art_pos,
            prev_article_key=prev_art_key,
            next_article_key=next_art_key,
            pg_meta=pg_meta
        )

        for text, meta in built:
            cid = meta["chunk_id"]
            if cid in seen_ids:
                quarantine_count += 1
                continue
            seen_ids.add(cid)
            upload_queue.append((text, meta))

    # Count general chunks quarantined
    for k, data in grouped.items():
        if data.get("is_general"):
            quarantine_count += 1

    logger.info(f"[Pipeline] {source_name}: upload_queue={len(upload_queue)} chunks, quarantined={quarantine_count}")


    if not upload_queue:
        logger.warning(f"[Pipeline] No valid chunks to upload for {source_name} — check article heading extraction")
        return 0, quarantine_count

    # ── Pre-upload validation gate (mirrors test script run_validation_gate) ──
    validation_errors = []
    seen_val_ids: set = set()
    for i, (vtext, vmeta) in enumerate(upload_queue):
        # 1. No empty text
        if not vtext.strip():
            validation_errors.append(f"[{i}] empty text (article={vmeta.get('article')})")
        # 2. No oversized chunks
        tok = vmeta.get("token_count", count_tokens(vtext))
        if tok > EMBEDDING_SAFE_LIMIT:
            validation_errors.append(f"[{i}] oversized chunk ({tok} tokens > {EMBEDDING_SAFE_LIMIT}) article={vmeta.get('article')}")
        # 3. No GENERAL bucket leaks
        if str(vmeta.get("article", "")).startswith("GENERAL"):
            validation_errors.append(f"[{i}] GENERAL bucket leaked to upload queue (source={vmeta.get('source')})")
        # 4. Missing source
        if not vmeta.get("source"):
            validation_errors.append(f"[{i}] missing 'source' field")
        # 5. Missing article
        if not vmeta.get("article"):
            validation_errors.append(f"[{i}] missing 'article' field (source={vmeta.get('source')})")
        # 6. Duplicate chunk_id
        cid = vmeta.get("chunk_id")
        if cid in seen_val_ids:
            validation_errors.append(f"[{i}] duplicate chunk_id '{cid}'")
        seen_val_ids.add(cid)

    if validation_errors:
        for err in validation_errors:
            logger.error(f"[Pipeline] Validation error: {err}")
        # Filter out invalid entries rather than halting; log count
        valid_queue = [(t, m) for t, m in upload_queue
                       if t.strip()
                       and not str(m.get("article", "")).startswith("GENERAL")
                       and m.get("source") and m.get("article")
                       and m.get("token_count", EMBEDDING_SAFE_LIMIT) <= EMBEDDING_SAFE_LIMIT]
        logger.warning(f"[Pipeline] {len(validation_errors)} validation issues; reduced queue from {len(upload_queue)} → {len(valid_queue)} chunks")
        upload_queue = valid_queue
        if not upload_queue:
            return 0, quarantine_count
    else:
        logger.info(f"[Pipeline] ✓ All {len(upload_queue)} chunks passed pre-upload validation")

    target_collection = collection_name or qdrant_manager.collection_name
    qdrant_manager.load_models()

    texts = [c[0] for c in upload_queue]
    metas = [c[1] for c in upload_queue]

    logger.info(f"Indexing {len(texts)} chunks to Qdrant collection '{target_collection}' with batch_size=4...")
    qdrant_manager.client.add(
        collection_name=target_collection,
        documents=texts,
        metadata=metas,
        batch_size=4
    )


    # Delete duplicate 'document' payload field injected by FastEmbed .add()
    try:
        qdrant_manager.client.delete_payload(
            collection_name=target_collection,
            keys=["document"],
            points=FilterSelector(filter=Filter())
        )
    except Exception as e:
        logger.warning(f"Failed to delete duplicate 'document' payload key: {e}")

    logger.info(f"Successfully indexed {len(texts)} chunks for {source_name}")
    return len(texts), quarantine_count

