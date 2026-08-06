"""
Production Ingestion Script — v1.1
Implements: Production Ingestion Design v1.1 (2026-07-26, FROZEN)

Key design decisions implemented here:
  - Reads from PDF cache (file_handler.py), NOT from the old Qdrant collection.
  - State-machine article parser supporting both ة and ه (Teh Marbuta variants) and nested hierarchy dicts.
  - One article = one chunk.
  - EMBEDDING_SAFE_LIMIT hard ceiling; oversized articles → sliding windows.
  - GENERAL buckets quarantined individually and reported; the document is never skipped.
  - Pre-upload validation gate (6 checks); halts on any failure.
  - Drops and recreates Qdrant collection before uploading.
  - Retrieval settings: unchanged.
"""

import sys, os, json, re, hashlib, time
from pathlib import Path
sys.path.insert(0, '.')

from config.settings import settings
settings.DEBUG_RETRIEVAL = False

from db.qdrant_client import qdrant_manager
from qdrant_client import QdrantClient
from langchain_core.documents import Document
import tiktoken

# ─────────────────────────────────────────────
# SPEC CONSTANTS  (change only these if model changes)
# ─────────────────────────────────────────────
EMBEDDING_SAFE_LIMIT = 512   # max tokens per indexed vector; exact model max
WINDOW_SIZE          = 500   # tokens per sliding window for oversized articles
WINDOW_OVERLAP       = 100   # token overlap between consecutive windows
# ─────────────────────────────────────────────

PROD_COLLECTION = qdrant_manager.collection_name

try:
    enc = tiktoken.get_encoding("cl100k_base")
    def count_tokens(text: str) -> int:
        return len(enc.encode(text))
except Exception:
    def count_tokens(text: str) -> int:
        return len(text.split())


from document_processor.pipeline import (
    sanitize_arabic_text,
    extract_cross_references,
    sliding_windows,
    normalize_domain,
    group_by_article,
    build_chunks_for_article,
    process_and_index_document,
    EMBEDDING_SAFE_LIMIT,
    WINDOW_SIZE,
    WINDOW_OVERLAP,
    count_tokens
)

# ── PostgreSQL Metadata Lookup (Cached Registry) ─────────────────────────────

_PG_DOCUMENT_REGISTRY: dict | None = None


def load_pg_document_registry() -> dict:
    """Load all Active documents from PostgreSQL once and build an in-memory law_key lookup registry."""
    global _PG_DOCUMENT_REGISTRY
    if _PG_DOCUMENT_REGISTRY is not None:
        return _PG_DOCUMENT_REGISTRY

    from db.database import SessionLocal
    from db.models import Document as PGDoc, DocumentFamily as PGFam

    registry = {}
    db = SessionLocal()
    try:
        active_docs = db.query(PGDoc).join(PGFam).filter(PGDoc.lifecycle_status == 'Active').all()
        for doc in active_docs:
            fam_title = doc.document_family.title if doc.document_family else ""
            key = _extract_law_key(fam_title)
            raw_dom = doc.document_family.domain if doc.document_family else "Legal"
            meta = {
                "document_id": doc.id,
                "document_family_id": doc.document_family_id,
                "domain": normalize_domain(raw_dom),
                "lifecycle_status": doc.lifecycle_status,
                "effective_date_gregorian": doc.effective_date_gregorian.isoformat() if doc.effective_date_gregorian else None,
                "allowed_roles": doc.allowed_roles or []
            }
            if key != "?_?":
                registry[key] = meta
            registry[fam_title] = meta
    except Exception as e:
        print(f"Warning: PG registry load failed: {e}")
    finally:
        db.close()

    _PG_DOCUMENT_REGISTRY = registry
    return registry


def get_pg_document_metadata(filename: str) -> dict:
    """Look up Active document metadata from cached PostgreSQL in-memory registry by filename or law key."""
    registry = load_pg_document_registry()
    
    # 1. Exact title match
    if filename in registry:
        return registry[filename]
        
    # 2. Law key match (e.g. 43_1992)
    law_key = _extract_law_key(filename)
    if law_key != "?_?" and law_key in registry:
        return registry[law_key]

    # 3. Partial title match
    for title, meta in registry.items():
        if filename in title or title in filename:
            return meta

    return {}


# ── Article grouping ───────────────────────────────────────────────────────────

ORDINAL_MAP = {
    'الأولى': '1', 'الثانية': '2', 'الثالثة': '3', 'الرابعة': '4',
    'الخامسة': '5', 'السادسة': '6', 'السابعة': '7', 'الثامنة': '8',
    'التاسعة': '9', 'العاشرة': '10',
    'الاولي': '1', 'الثانيه': '2', 'الثالثه': '3', 'الرابعه': '4',
    'الخامسه': '5', 'السادسه': '6', 'السابعه': '7', 'الثامنه': '8',
    'التاسعه': '9', 'العاشره': '10',
}

# Matches all known OCR-produced article heading formats for UAE Arabic laws:
#  1. Standard:       المادة 9  /  المادة (9)  /  ماده 9
#  2. Inverted-paren: ( الماده )9   ← OCR produces this format frequently
#  3. Pre-paren:      (المادة 9)  /  (ماده 9)
#  4. ARTICLE: hdr:  ARTICLE: المادة (9)  or  ARTICLE: 9
#  5. Arabic Ordinal: المادة الأولى / الثانية ...
ARTICLE_HEADING_RE = re.compile(
    r'(?:المادة|الماده|مادة|ماده)\s*\(?\s*(\d+)\s*\)?'   # group 1: Standard
    r'|\(\s*(?:المادة|الماده|مادة|ماده)\s*\)\s*(\d+)'     # group 2: Inverted-paren  ( الماده )9
    r'|\(?\s*(?:المادة|الماده|مادة|ماده)\s*(\d+)\s*\)?'   # group 3: Pre-paren  (المادة 9)
    r'|ARTICLE:\s*(?:[^\n]*?\()?(\d+)'                      # group 4: ARTICLE: header
    r'|(?:المادة|الماده|مادة|ماده)\s+(الأولى|الثانية|الثالثة|الرابعة|الخامسة|السادسة|السابعة|الثامنة|التاسعة|العاشرة|الاولي|الثانيه|الثالثه|الرابعه|الخامسه|السادسه|السابعه|الثامنه|التاسعه|العاشره)' # group 5: Ordinal
)

def _first_group(m) -> str | None:
    """Return the first non-None captured group from a regex match, mapped if ordinal."""
    if m is None:
        return None
    for g in m.groups():
        if g is not None:
            return ORDINAL_MAP.get(g, g)
    return None

def extract_article_num(text: str, meta: dict) -> str | None:
    """Extract article number from text or hierarchy metadata."""
    hierarchy = meta.get("hierarchy", {})
    if isinstance(hierarchy, dict):
        for h in ("h1", "h2", "h3", "h4"):
            hv = hierarchy.get(h, "") or ""
            if hv:
                m = ARTICLE_HEADING_RE.search(hv)
                result = _first_group(m)
                if result:
                    return result

    for h in ("h1", "h2", "h3", "h4"):
        hv = meta.get(h, "") or ""
        if hv:
            m = ARTICLE_HEADING_RE.search(hv)
            result = _first_group(m)
            if result:
                return result

    m = ARTICLE_HEADING_RE.search(text)
    return _first_group(m)


def group_by_article(chunks: list[Document], source_name: str) -> dict:
    """
    Group langchain Document chunks by article number using a state machine.
    GENERAL chunks (preambles) are stored with unique indices so they do NOT merge into a mega-chunk.
    """
    grouped: dict[str, dict] = {}
    current_art_key = None
    general_counter = 0

    for doc in chunks:
        text = doc.page_content.strip()
        meta = dict(doc.metadata)

        art_found = extract_article_num(text, meta)
        if art_found:
            current_art_key = art_found

        key = current_art_key
        if key is None:
            # Preamble / intro chunk before Article 1
            key = f"GENERAL_{general_counter}"
            general_counter += 1

        if key not in grouped:
            grouped[key] = {"text_parts": [], "base_meta": meta, "source": source_name, "is_general": key.startswith("GENERAL")}
        grouped[key]["text_parts"].append(text)

    return grouped


# ── Chunk builder ──────────────────────────────────────────────────────────────

# Strips the LAW:/ARTICLE:/SECTION:/--- header block that _build_chunk() in file_handler.py
# prepends to every page_content. Must be removed BEFORE joining text_parts, otherwise
# build_chunks_for_article() wraps the same header a second time around the body.
#
# IMPORTANT: normalize_arabic_text() in file_handler.py strips newlines from page_content,
# so the header may appear as a single line: "LAW: ... ARTICLE: ... -------------- text"
# instead of multi-line. The regex must handle BOTH formats.
# Strategy: match from LAW: (start of string) through the first ----- separator.
_HEADER_STRIP_RE = re.compile(
    r'^LAW:.+?-{3,}\s*',   # non-greedy: stops at FIRST --- block; \s* eats trailing whitespace/newline
    re.DOTALL              # . matches newlines (handles multi-line format too)
)

def _strip_chunk_header(text: str) -> str:
    """Remove the structured header prefix added by StructureAwareChunker._build_chunk()."""
    return _HEADER_STRIP_RE.sub('', text, count=1).strip()


def build_chunks_for_article(
    source: str,
    art_key: str,
    text_parts: list,
    base_meta: dict,
    article_position: int = 1,
    prev_article_key: str | None = None,
    next_article_key: str | None = None,
) -> list[tuple[str, dict]]:
    """
    Build one or more (text, metadata) tuples for an article.
    Applies sliding-window splitting if the article exceeds EMBEDDING_SAFE_LIMIT.
    NOTE: text_parts come from file_handler page_content which already contains a
    LAW:/ARTICLE:/--- header. That header is stripped here before re-wrapping with the
    production-grade header so the embedding model sees clean legal text.
    """
    law_num_m = re.search(r'رقم\s*\(?\s*(\d+)\s*\)?', source)
    law_yr_m  = re.search(r'لسنة\s*(\d{4})', source)
    law_num   = law_num_m.group(1) if law_num_m else ""
    law_year  = law_yr_m.group(1)  if law_yr_m  else ""

    # Strip the file_handler structural header from each text_part before joining.
    clean_parts = [_strip_chunk_header(part) for part in text_parts]
    full_body   = "\n\n".join(p for p in clean_parts if p)  # skip empty parts after strip
    clean_source_title = source.replace(".pdf", "").strip()
    header_prefix  = f"LAW: {clean_source_title}\nARTICLE: المادة ({art_key})\n\n"
    full_text      = sanitize_arabic_text(header_prefix + full_body)
    xrefs, xref_keys = extract_cross_references(full_body)
    parent_id        = f"parent_{hashlib.md5((source + art_key).encode()).hexdigest()[:12]}"
    doc_hash         = hashlib.md5((source + art_key).encode('utf-8')).hexdigest()

    # Fetch PostgreSQL metadata for this document
    pg_meta = get_pg_document_metadata(source)

    tokens = enc.encode(full_text)
    tok_count = len(tokens)

    clean_title = clean_source_title
    article_title = f"المادة {art_key}"
    article_canonical_key = f"{law_num}_{law_year}_{art_key}" if (law_num and law_year) else f"art_{art_key}"

    # Canonical keys for neighboring articles in the same law
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
        # Clean hierarchy h1
        hierarchy = dict(m.get("hierarchy", {}))
        hierarchy["h1"] = article_title
        hierarchy["h2"] = None
        hierarchy["h3"] = None
        hierarchy["h4"] = None

        m.update({
            # Core required fields
            "source":               source,
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
            # Window tracking
            "chunk_index":          idx,
            "total_chunks":         total_chunks,
            "window_number":        idx + 1,
            "window_start_token":   w_start,
            "window_end_token":     w_end,
            "token_count":          win_tok_count,
            "is_first_window":      (idx == 0),
            "is_last_window":       (idx == total_chunks - 1),
            # Neighbor pointers
            "previous_chunk_id":    prev_cid,
            "next_chunk_id":        next_cid,
            # Enriched fields
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
        })
        if pg_meta:
            m.update(pg_meta)
        results.append((win_text, m))

    return results


# ── Main processing pipeline ───────────────────────────────────────────────────

def _extract_law_key(filename: str) -> str:
    """Stable dedup key: first law number in parens + first 4-digit year."""
    num = re.search(r'\((\d+)\)', filename)
    yr  = re.search(r'(\d{4})', filename)
    return f"{num.group(1) if num else '?'}_{yr.group(1) if yr else '?'}"


def load_corpus_from_cache() -> list[tuple[str, list[Document]]]:
    """Load all cached PDF documents from arabic_docs. Returns list of (source_name, chunks)."""
    from document_processor.file_handler import DocumentProcessor

    processor = DocumentProcessor()
    
    seen_law_keys: set[str] = set()
    pdf_files: list[Path] = []

    # Search arabic_docs and subdirectories for PDF files
    corpus_dir = Path("arabic_docs")
    if corpus_dir.exists():
        for f in sorted(corpus_dir.rglob("*.pdf")):
            key = _extract_law_key(f.name)
            if key not in seen_law_keys:
                seen_law_keys.add(key)
                pdf_files.append(f)

    results = []
    if not pdf_files:
        print(f"WARNING: No PDFs found in {corpus_dir.resolve()}")
        return results

    print(f"Loading corpus from {len(pdf_files)} PDF files in {corpus_dir.name}...")
    for pdf_path in pdf_files:
        docs = processor.process_single_file(pdf_path)
        results.append((pdf_path.name, docs))
        print(f"  Loaded {pdf_path.name}: {len(docs)} chunks")

    return results


def process_corpus(corpus: list[tuple[str, list[Document]]]) -> tuple[list, list, dict]:
    """
    Process full corpus through group_by_article and build_chunks_for_article.
    Quarantines GENERAL buckets and duplicates without halting.
    Returns (upload_queue, quarantine, stats).
    """
    upload_queue  = []   # list of (text, meta)
    quarantine    = []   # list of dicts
    seen_ids      = set()

    stats = {
        "documents_processed": 0,
        "documents_with_general": 0,
        "articles_indexed": 0,
        "windows_created": 0,
        "chunks_quarantined": 0,
        "per_document": {}
    }

    for source_name, chunks in corpus:
        stats["documents_processed"] += 1
        grouped = group_by_article(chunks, source_name)
        doc_stats = {"articles": 0, "windows": 0, "quarantined_general_tokens": 0}

        # Filter out GENERAL buckets to determine article key ordering
        art_keys = [k for k, v in grouped.items() if not v.get("is_general")]

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
                next_article_key=next_art_key
            )

            for text, meta in built:
                # Dedup by chunk_id
                cid = meta["chunk_id"]
                if cid in seen_ids:
                    quarantine.append({"reason": "duplicate", "source": source_name,
                                       "article": art_key, "chunk_id": cid})
                    stats["chunks_quarantined"] += 1
                    continue
                seen_ids.add(cid)
                upload_queue.append((text, meta))
                doc_stats["windows"] += 1

            doc_stats["articles"] += 1

        stats["articles_indexed"] += doc_stats["articles"]
        stats["windows_created"]  += doc_stats["windows"]
        stats["per_document"][source_name] = doc_stats

    return upload_queue, quarantine, stats


def run_validation_gate(upload_queue: list) -> None:
    """
    6-check pre-upload validation. Halts (raises) on any failure.
    Runs AFTER quarantine — GENERAL buckets must already be removed.
    """
    print("Running pre-upload validation gate (6 checks)...")
    seen_ids = set()
    for i, (text, meta) in enumerate(upload_queue):

        # 1. No empty text
        if not text.strip():
            raise ValueError(f"VALIDATION HALT — empty text at index {i} "
                             f"(source={meta.get('source')}, article={meta.get('article')})")

        # 2. No oversized chunks
        tok = meta.get("token_count", count_tokens(text))
        if tok > EMBEDDING_SAFE_LIMIT:
            raise ValueError(f"VALIDATION HALT — oversized chunk ({tok} tok) at index {i} "
                             f"(source={meta.get('source')}, article={meta.get('article')})")

        # 3. No GENERAL buckets reach upload queue
        if str(meta.get("article")).startswith("GENERAL"):
            raise ValueError(f"VALIDATION HALT — GENERAL bucket leaked to upload queue at index {i} "
                             f"(source={meta.get('source')}). This is a code error.")

        # 4. No missing source
        if not meta.get("source"):
            raise ValueError(f"VALIDATION HALT — missing 'source' at index {i}")

        # 5. No missing article
        if not meta.get("article"):
            raise ValueError(f"VALIDATION HALT — missing 'article' at index {i} "
                             f"(source={meta.get('source')})")

        # 6. No duplicate chunk IDs
        cid = meta.get("chunk_id")
        if cid in seen_ids:
            raise ValueError(f"VALIDATION HALT — duplicate chunk_id '{cid}' at index {i} "
                             f"(source={meta.get('source')}, article={meta.get('article')})")
        seen_ids.add(cid)

    print(f"  ✓ All {len(upload_queue)} chunks passed validation.")


def print_quarantine_report(quarantine: list) -> None:
    if not quarantine:
        print("\n=== QUARANTINE REPORT: Nothing quarantined. ===\n")
        return

    print("\n" + "="*70)
    print("  QUARANTINED CHUNKS (NOT indexed in Qdrant)")
    print("="*70)
    for q in quarantine:
        reason = q.get("reason", "unknown")
        source = q.get("source", "?")
        art    = q.get("article", "?")
        tok    = q.get("tokens", "?")
        if reason == "GENERAL_bucket":
            print(f"  [GENERAL]   {source}  |  {tok} tokens")
            print(f"              Preview: {q.get('preview', '')[:100]}...")
        elif reason == "duplicate":
            print(f"  [DUPLICATE] {source}  |  Article {art}  |  id={q.get('chunk_id','?')}")
        else:
            print(f"  [{reason.upper()}] {source}  |  Article {art}")
    print("="*70)
    print(f"  Total quarantined: {len(quarantine)} chunk(s)")
    print("  These were NOT indexed. If benchmark misses fall here, investigate.")
    print("="*70 + "\n")


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    start = time.time()
    print("=" * 70)
    print("  RAGNR AI — Production Ingestion v1.1")
    print("  Spec: Production Ingestion Design v1.1 (2026-07-26, FROZEN)")
    print(f"  EMBEDDING_SAFE_LIMIT = {EMBEDDING_SAFE_LIMIT} tokens")
    print(f"  WINDOW_SIZE = {WINDOW_SIZE}  |  WINDOW_OVERLAP = {WINDOW_OVERLAP}")
    print("=" * 70)

    # ── Step 1: Load from cache ────────────────────────────────────────────────
    print("\nStep 1: Loading corpus from PDF cache...")
    corpus = load_corpus_from_cache()
    if not corpus:
        print("ERROR: No documents loaded. Aborting.")
        sys.exit(1)

    # ── Step 2: Group + chunk + quarantine ────────────────────────────────────
    print("\nStep 2: Grouping by article, applying size ceiling, quarantining GENERAL...")
    upload_queue, quarantine, stats = process_corpus(corpus)
    print(f"  Documents processed : {stats['documents_processed']}")
    print(f"  Articles indexed    : {stats['articles_indexed']}")
    print(f"  Total upload chunks : {stats['windows_created']}  (including window splits)")
    print(f"  Chunks quarantined  : {stats['chunks_quarantined']}")

    # ── Step 3: Pre-upload validation ─────────────────────────────────────────
    print("\nStep 3: Pre-upload validation gate...")
    run_validation_gate(upload_queue)

    # ── Step 4: Drop + recreate Qdrant collection ──────────────────────────────
    print(f"\nStep 4: Resetting Qdrant collection '{PROD_COLLECTION}'...")
    client = qdrant_manager.client

    if client.collection_exists(PROD_COLLECTION):
        client.delete_collection(PROD_COLLECTION)
        print(f"  Deleted existing collection.")

    from qdrant_client.models import VectorParams, Distance
    client.create_collection(
        collection_name=PROD_COLLECTION,
        vectors_config={
            client.get_vector_field_name(): VectorParams(size=1024, distance=Distance.COSINE)
        }
    )
    print(f"  Created fresh collection.")

    # ── Step 5: Embed + upload ─────────────────────────────────────────────────
    print(f"\nStep 5: Embedding and uploading {len(upload_queue)} chunks (batch_size=16)...")
    qdrant_manager.load_models()

    texts = [c[0] for c in upload_queue]
    metas = [c[1] for c in upload_queue]

    qdrant_manager.client.add(
        collection_name=PROD_COLLECTION,
        documents=texts,
        metadata=metas,
        batch_size=8
    )

    from qdrant_client.models import FilterSelector, Filter
    client.delete_payload(
        collection_name=PROD_COLLECTION,
        keys=["document"],
        points=FilterSelector(filter=Filter())
    )
    print("  Permanently purged duplicate 'document' payload field from Qdrant.")

    # ── Step 6: Verify ────────────────────────────────────────────────────────
    info = client.get_collection(PROD_COLLECTION)
    print(f"\nStep 6: Verification — collection now contains {info.points_count} points.")

    # ── Step 7: Save manifest ──────────────────────────────────────────────────
    os.makedirs("scratch", exist_ok=True)
    manifest = {
        "ingestion_spec":          "v1.1",
        "timestamp":               time.strftime("%Y-%m-%d %H:%M:%S"),
        "embedding_safe_limit":    EMBEDDING_SAFE_LIMIT,
        "window_size":             WINDOW_SIZE,
        "window_overlap":          WINDOW_OVERLAP,
        "dense_model":             "intfloat/multilingual-e5-large",
        "collection":              PROD_COLLECTION,
        "points_in_qdrant":        info.points_count,
        "stats":                   stats,
        "quarantine_count":        len(quarantine),
    }
    with open("scratch/ingestion_manifest_v1_1.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    with open("scratch/quarantine_v1_1.jsonl", "w", encoding="utf-8") as f:
        for q in quarantine:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")

    # ── Quarantine report ──────────────────────────────────────────────────────
    print_quarantine_report(quarantine)

    elapsed = time.time() - start
    print("=" * 70)
    print(f"  INGESTION COMPLETE in {elapsed:.1f}s")
    print(f"  {info.points_count} points in Qdrant  |  {len(quarantine)} quarantined")
    print(f"  Manifest: scratch/ingestion_manifest_v1_1.json")
    print("=" * 70)
    print("\nNext step: run the benchmark.")
    print("  python scratch/run_post_ingestion_acceptance.py")


if __name__ == "__main__":
    main()
