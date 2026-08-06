import time
import math
import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from enum import Enum

from langchain_core.retrievers import BaseRetriever
from langchain_core.documents import Document
from db.qdrant_client import qdrant_manager
from config.settings import settings
from utils.logging import retrieval_logger as logger
from utils.query_normalizer import normalize_query_text
from utils.query_entity_extractor import extract_query_entities, ExtractedEntities
from retriever.grouping import CandidateGrouper

from qdrant_client.models import (
    Filter, FieldCondition, MatchValue, MatchAny, IsEmptyCondition, PayloadField, Range
)

# FlashRank initialization
try:
    from flashrank import Ranker, RerankRequest
    from pathlib import Path
    
    cache_dir = Path(__file__).parent.parent / ".cache" / "flashrank"
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        ranker = Ranker(cache_dir=str(cache_dir))
    except Exception as e:
        logger.error(f"Failed to initialize Ranker: {e}")
        ranker = None
except ImportError:
    logger.warning("flashrank not installed. Reranking will be disabled.")
    ranker = None


class CandidateSource(str, Enum):
    DENSE = "DENSE"
    METADATA_EXACT = "METADATA_EXACT"
    DUAL_PROVIDER = "DUAL_PROVIDER"
    NEIGHBOR_EXPANSION = "NEIGHBOR_EXPANSION"


class QdrantHitWrapper:
    def __init__(self, hit_id: str, score: float, document: str, metadata: dict, source: CandidateSource):
        self.id = hit_id
        self.score = score
        self.document = document
        self.metadata = metadata
        self.source = source


class QdrantHybridRetriever(BaseRetriever):
    k: int = settings.VECTOR_SEARCH_K
    final_k: int = settings.RERANKER_TOP_K
    threshold: float = settings.RERANKER_THRESHOLD

    tenant_id: str = "default_tenant"
    thread_id: Optional[str] = None
    document_ids: Optional[List[str]] = None
    allowed_roles: Optional[List[str]] = None
    applicability: Optional[dict] = None
    domain: Optional[str] = None
    intent_type: Optional[str] = None
    target_date: Optional[str] = None
    w_dense: float = 0.7
    w_sparse: float = 0.3
    debug_mode: bool = True
    last_trace: dict = field(default_factory=dict)

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> List[Document]:
        start_time = time.time()
        trace_data = {
            "query": query,
            "normalized_query": "",
            "entities": {},
            "dense_latency_ms": 0.0,
            "metadata_latency_ms": 0.0,
            "merge_latency_ms": 0.0,
            "rerank_latency_ms": 0.0,
            "total_latency_ms": 0.0,
            "provenance_breakdown": {},
            "confidence_score": 0.0,
            "confidence_level": "LOW",
            "selected_results": []
        }

        try:
            # 1. Normalize Query Text & Extract Entities
            norm_query = normalize_query_text(query)
            entities = extract_query_entities(norm_query)
            trace_data["normalized_query"] = norm_query
            trace_data["entities"] = {
                "articles": entities.articles,
                "law_numbers": entities.law_numbers,
                "law_years": entities.law_years,
                "document_types": entities.document_types,
                "article_keys": entities.article_keys,
                "has_citation": entities.has_citation
            }

            # 2. Build Base Qdrant Filter
            must_conditions = [
                FieldCondition(key="tenant_id", match=MatchValue(value=self.tenant_id))
            ]
            if self.thread_id:
                must_conditions.append(Filter(should=[
                    FieldCondition(key="thread_id", match=MatchValue(value=self.thread_id)),
                    IsEmptyCondition(is_empty=PayloadField(key="thread_id"))
                ]))
            if self.document_ids:
                must_conditions.append(FieldCondition(key="document_id", match=MatchAny(any=self.document_ids)))
            if self.domain and self.domain.lower() != "legal":
                must_conditions.append(FieldCondition(key="domain", match=MatchValue(value=self.domain)))

            role_conditions = [IsEmptyCondition(is_empty=PayloadField(key="allowed_roles"))]
            if self.allowed_roles:
                role_conditions.append(FieldCondition(key="allowed_roles", match=MatchAny(any=self.allowed_roles)))
            must_conditions.append(Filter(should=role_conditions))

            intent = (self.intent_type or "").upper()
            if intent == "HISTORICAL" and self.target_date:
                target_dt = self.target_date
                if len(target_dt) == 4 and target_dt.isdigit():
                    target_dt = f"{target_dt}-12-31"
                must_conditions.append(FieldCondition(key="effective_date_gregorian", range=Range(lte=target_dt)))
                must_conditions.append(Filter(should=[
                    IsEmptyCondition(is_empty=PayloadField(key="expiry_date_gregorian")),
                    FieldCondition(key="expiry_date_gregorian", range=Range(gt=target_dt))
                ]))
            base_filter = Filter(must=must_conditions) if must_conditions else None
            qdrant_manager.load_models()

            # 3. Provider A: Dense Vector Search (Always runs)
            t_d0 = time.time()
            dense_hits = []
            try:
                raw_dense = qdrant_manager.client.query(
                    collection_name=qdrant_manager.collection_name,
                    query_text=norm_query,
                    query_filter=base_filter,
                    limit=settings.VECTOR_SEARCH_K
                )
                for hit in raw_dense:
                    payload = getattr(hit, "metadata", None) or getattr(hit, "payload", None) or {}
                    doc_text = payload.get("text") or payload.get("document", "")
                    cid = str(getattr(hit, "id", None) or payload.get("chunk_id") or hash(doc_text))
                    meta = {k: v for k, v in payload.items() if k not in ["document", "text", "page_content"]}
                    meta["id"] = cid
                    dense_hits.append(QdrantHitWrapper(cid, hit.score, doc_text, meta, CandidateSource.DENSE))
            except Exception as e:
                logger.error(f"[DenseProvider] Search error: {e}")
            trace_data["dense_latency_ms"] = (time.time() - t_d0) * 1000

            # 4. Provider B: Deterministic Metadata Search (Always runs in parallel)
            t_m0 = time.time()
            metadata_hits = []
            if entities.has_citation:
                try:
                    meta_conditions = list(must_conditions)
                    if entities.articles:
                        meta_conditions.append(FieldCondition(key="article", match=MatchAny(any=entities.articles)))
                    if entities.law_numbers:
                        meta_conditions.append(FieldCondition(key="law_number", match=MatchAny(any=entities.law_numbers)))
                    if entities.law_years:
                        meta_conditions.append(FieldCondition(key="law_year", match=MatchAny(any=entities.law_years)))

                    meta_filter = Filter(must=meta_conditions)
                    scroll_res, _ = qdrant_manager.client.scroll(
                        collection_name=qdrant_manager.collection_name,
                        scroll_filter=meta_filter,
                        limit=settings.METADATA_SEARCH_K,
                        with_payload=True,
                        with_vectors=False
                    )
                    for pt in scroll_res:
                        payload = pt.payload or {}
                        doc_text = payload.get("text") or payload.get("document", "")
                        cid = str(pt.id or payload.get("chunk_id") or hash(doc_text))
                        meta = {k: v for k, v in payload.items() if k not in ["document", "text", "page_content"]}
                        meta["id"] = cid
                        metadata_hits.append(QdrantHitWrapper(cid, 1.0, doc_text, meta, CandidateSource.METADATA_EXACT))
                except Exception as e:
                    logger.error(f"[MetadataProvider] Search error: {e}")
            trace_data["metadata_latency_ms"] = (time.time() - t_m0) * 1000

            # 5. Candidate Merger & Multi-Factor Provenance Scoring
            t_merge0 = time.time()
            merged_map: Dict[str, QdrantHitWrapper] = {}
            dense_ids = {h.id for h in dense_hits}
            meta_ids = {h.id for h in metadata_hits}
            dual_ids = dense_ids.intersection(meta_ids)

            for h in dense_hits:
                src = CandidateSource.DUAL_PROVIDER if h.id in dual_ids else CandidateSource.DENSE
                merged_map[h.id] = QdrantHitWrapper(h.id, h.score, h.document, h.metadata, src)

            for h in metadata_hits:
                if h.id not in merged_map:
                    merged_map[h.id] = QdrantHitWrapper(h.id, 0.5, h.document, h.metadata, CandidateSource.METADATA_EXACT)
                else:
                    merged_map[h.id].source = CandidateSource.DUAL_PROVIDER

            # Multi-Factor Score Boost Calculation (Gated: Apply entity boosts only when explicit citations are present)
            if entities.has_citation:
                for cid, cand in merged_map.items():
                    boost = 0.0
                    meta = cand.metadata

                    art_val = str(meta.get("article", ""))
                    law_val = str(meta.get("law_number", ""))
                    yr_val = str(meta.get("law_year", ""))
                    dtype_val = str(meta.get("document_type", ""))

                    if art_val and art_val in entities.articles:
                        boost += settings.ARTICLE_MATCH_BOOST
                    if law_val and law_val in entities.law_numbers:
                        boost += settings.LAW_MATCH_BOOST
                    if yr_val and yr_val in entities.law_years:
                        boost += settings.YEAR_MATCH_BOOST
                    if dtype_val and dtype_val in entities.document_types:
                        boost += settings.DOC_TYPE_MATCH_BOOST
                    if cand.source == CandidateSource.DUAL_PROVIDER:
                        boost += settings.DUAL_PROVIDER_PROVENANCE_BONUS

                    cand.score += boost

            # 6. Smart Context-Aware Neighbor Expansion
            has_expansion_kw = any(kw in norm_query.lower() for kw in settings.NEIGHBOR_EXPANSION_KEYWORDS)
            neighbor_keys_to_fetch = set()

            for cand in list(merged_map.values()):
                total_chunks = cand.metadata.get("total_chunks", 1)
                prev_key = cand.metadata.get("previous_article_key")
                next_key = cand.metadata.get("next_article_key")

                # Expand if multi-window article OR query contains context keywords
                if (total_chunks > 1 or has_expansion_kw) and settings.ENABLE_NEIGHBOR_EXPANSION:
                    if prev_key: neighbor_keys_to_fetch.add(prev_key)
                    if next_key: neighbor_keys_to_fetch.add(next_key)

            if neighbor_keys_to_fetch:
                try:
                    n_filter = Filter(must=[FieldCondition(key="article_key", match=MatchAny(any=list(neighbor_keys_to_fetch)))])
                    n_pts, _ = qdrant_manager.client.scroll(
                        collection_name=qdrant_manager.collection_name,
                        scroll_filter=n_filter,
                        limit=20,
                        with_payload=True,
                        with_vectors=False
                    )
                    for pt in n_pts:
                        payload = pt.payload or {}
                        doc_text = payload.get("text") or payload.get("document", "")
                        cid = str(pt.id or payload.get("chunk_id") or hash(doc_text))
                        if cid not in merged_map:
                            meta = {k: v for k, v in payload.items() if k not in ["document", "text", "page_content"]}
                            meta["id"] = cid
                            merged_map[cid] = QdrantHitWrapper(cid, 0.4, doc_text, meta, CandidateSource.NEIGHBOR_EXPANSION)
                except Exception as ne_err:
                    logger.warning(f"Neighbor expansion lookup failed: {ne_err}")

            raw_sorted = sorted(merged_map.values(), key=lambda x: x.score, reverse=True)[:settings.CANDIDATE_POOL_TOP_K]
            
            # Step 6b: Execute Candidate Grouping & Duplicate Consolidation
            grouper = CandidateGrouper(score_aggregation="max", similarity_threshold=0.92)
            grouped_cands, group_metrics = grouper.group_candidates(raw_sorted, target_k=self.final_k)
            
            trace_data["merge_latency_ms"] = (time.time() - t_merge0) * 1000
            trace_data["grouping_metrics"] = group_metrics

            # Provenance breakdown logging
            prov_counts = {}
            for g in grouped_cands:
                prov_counts[g.source_provenance] = prov_counts.get(g.source_provenance, 0) + 1
            trace_data["provenance_breakdown"] = prov_counts

            # Convert GroupedCandidates to Langchain Documents for Reranker
            docs = []
            passages = []
            for idx, gcand in enumerate(grouped_cands):
                meta = dict(gcand.metadata)
                meta["candidate_source"] = gcand.source_provenance
                meta["pre_rerank_score"] = gcand.score
                meta["group_id"] = gcand.group_id
                meta["article_key"] = gcand.article_key
                doc = Document(page_content=gcand.document_text, metadata=meta)
                docs.append(doc)
                passages.append({"id": idx, "text": gcand.document_text, "meta": meta})

            # 7. FlashRank Cross-Encoder Reranking
            # Note: ms-marco-MiniLM-L-6-v2 is English-only. For Arabic queries, preserve vector cosine + metadata boost ranking.
            t_r0 = time.time()
            final_docs = []
            is_arabic = any('\u0600' <= char <= '\u06FF' for char in norm_query)

            if ranker and passages and not is_arabic:
                try:
                    req = RerankRequest(query=norm_query, passages=passages)
                    reranked = ranker.rerank(req)
                    for rank_idx, r_item in enumerate(reranked[:self.final_k]):
                        orig_idx = r_item["id"]
                        score = float(r_item.get("score", 0.0))
                        orig_doc = docs[orig_idx]
                        orig_doc.metadata["rerank_score"] = score
                        final_docs.append(orig_doc)
                except Exception as r_err:
                    logger.error(f"FlashRank reranking error: {r_err}")
                    final_docs = docs[:self.final_k]
            else:
                final_docs = docs[:self.final_k]
                for d in final_docs:
                    d.metadata["rerank_score"] = d.metadata.get("pre_rerank_score", 0.5)

            trace_data["rerank_latency_ms"] = (time.time() - t_r0) * 1000

            # 8. Numeric Confidence Score Calculation
            top_score = final_docs[0].metadata.get("rerank_score", 0.5) if final_docs else 0.0
            has_meta_hit = any(d.metadata.get("candidate_source") in [CandidateSource.METADATA_EXACT.value, CandidateSource.DUAL_PROVIDER.value] for d in final_docs)
            conf_score = min(1.0, max(0.0, (top_score * 0.7) + (0.3 if has_meta_hit else 0.0)))
            trace_data["confidence_score"] = round(conf_score, 4)
            trace_data["confidence_level"] = "HIGH" if conf_score >= 0.75 else ("MEDIUM" if conf_score >= 0.40 else "LOW")

            trace_data["total_latency_ms"] = (time.time() - start_time) * 1000
            trace_data["selected_results"] = [
                {"score": d.metadata.get("rerank_score", 0.0), "source": d.metadata.get("candidate_source"), "article": d.metadata.get("article_title")}
                for d in final_docs
            ]

            # Structured Retrieval Decision Report for Observability
            trace_data["decision_report"] = {
                "normalized_query": norm_query,
                "entities": {
                    "articles": entities.articles,
                    "law_numbers": entities.law_numbers,
                    "law_years": entities.law_years,
                    "document_types": entities.document_types
                },
                "providers": {
                    "dense": {
                        "candidates_returned": len(dense_hits),
                        "latency_ms": round(trace_data["dense_latency_ms"], 1)
                    },
                    "metadata": {
                        "matched": bool(metadata_hits),
                        "candidates_returned": len(metadata_hits),
                        "latency_ms": round(trace_data["metadata_latency_ms"], 1)
                    }
                },
                "candidate_pool_size": len(raw_sorted),
                "neighbor_expansion_applied": bool(neighbor_keys_to_fetch),
                "neighbor_chunks_added": len([c for c in raw_sorted if c.source == CandidateSource.NEIGHBOR_EXPANSION]),
                "reranked_count": len(final_docs),
                "confidence_score": trace_data["confidence_score"],
                "confidence_level": trace_data["confidence_level"]
            }

            self.last_trace = trace_data
            logger.info(
                f"[Retriever] {len(final_docs)} chunks returned in {trace_data['total_latency_ms']:.1f}ms "
                f"(Dense: {trace_data['dense_latency_ms']:.1f}ms, Meta: {trace_data['metadata_latency_ms']:.1f}ms, Conf: {trace_data['confidence_level']})"
            )
            return final_docs

        except Exception as query_err:
            logger.error(f"QdrantHybridRetriever execution error: {query_err}")
            return []

    def _get_raw_candidates(self, query: str) -> List[Any]:
        """Returns raw Top 75 merged candidates from Dense & Metadata providers before Grouping/Reranking."""
        try:
            norm_query = normalize_query_text(query)
            entities = extract_query_entities(norm_query)

            dense_hits = []
            try:
                res = qdrant_manager.client.query(
                    collection_name=qdrant_manager.collection_name,
                    query_text=norm_query,
                    limit=settings.VECTOR_SEARCH_K
                )
                for hit in res:
                    meta = getattr(hit, "metadata", None) or getattr(hit, "payload", None) or {}
                    doc_text = meta.get("text") or meta.get("document", "")
                    cid = str(getattr(hit, "id", hash(doc_text)))
                    meta["id"] = cid
                    dense_hits.append(QdrantHitWrapper(cid, getattr(hit, "score", 0.0), doc_text, meta, CandidateSource.DENSE))
            except Exception as e:
                logger.error(f"[DenseProvider] Search error: {e}")

            metadata_hits = []
            if entities.has_citation:
                try:
                    must_conditions = []
                    if entities.articles:
                        must_conditions.append(FieldCondition(key="article", match=MatchAny(any=entities.articles)))
                    if entities.law_numbers:
                        must_conditions.append(FieldCondition(key="law_number", match=MatchAny(any=entities.law_numbers)))
                    if entities.law_years:
                        must_conditions.append(FieldCondition(key="law_year", match=MatchAny(any=entities.law_years)))

                    if must_conditions:
                        pts, _ = qdrant_manager.client.scroll(
                            collection_name=qdrant_manager.collection_name,
                            scroll_filter=Filter(must=must_conditions),
                            limit=settings.METADATA_SEARCH_K,
                            with_payload=True,
                            with_vectors=False
                        )
                        for pt in pts:
                            payload = pt.payload or {}
                            doc_text = payload.get("text") or payload.get("document", "")
                            cid = str(pt.id or payload.get("chunk_id") or hash(doc_text))
                            meta = {k: v for k, v in payload.items() if k not in ["document", "text", "page_content"]}
                            meta["id"] = cid
                            metadata_hits.append(QdrantHitWrapper(cid, 1.0, doc_text, meta, CandidateSource.METADATA_EXACT))
                except Exception as e:
                    logger.error(f"[MetadataProvider] Search error: {e}")

            merged_map: Dict[str, QdrantHitWrapper] = {}
            dense_ids = {h.id for h in dense_hits}
            meta_ids = {h.id for h in metadata_hits}
            dual_ids = dense_ids.intersection(meta_ids)

            for h in dense_hits:
                src = CandidateSource.DUAL_PROVIDER if h.id in dual_ids else CandidateSource.DENSE
                merged_map[h.id] = QdrantHitWrapper(h.id, h.score, h.document, h.metadata, src)

            for h in metadata_hits:
                if h.id not in merged_map:
                    merged_map[h.id] = QdrantHitWrapper(h.id, 0.5, h.document, h.metadata, CandidateSource.METADATA_EXACT)
                else:
                    merged_map[h.id].source = CandidateSource.DUAL_PROVIDER

            if entities.has_citation:
                for cid, cand in merged_map.items():
                    boost = 0.0
                    meta = cand.metadata
                    art_val = str(meta.get("article", ""))
                    law_val = str(meta.get("law_number", ""))
                    yr_val = str(meta.get("law_year", ""))
                    if art_val and art_val in entities.articles: boost += settings.ARTICLE_MATCH_BOOST
                    if law_val and law_val in entities.law_numbers: boost += settings.LAW_MATCH_BOOST
                    if yr_val and yr_val in entities.law_years: boost += settings.YEAR_MATCH_BOOST
                    if cand.source == CandidateSource.DUAL_PROVIDER: boost += settings.DUAL_PROVIDER_PROVENANCE_BONUS
                    cand.score += boost

            return sorted(merged_map.values(), key=lambda x: x.score, reverse=True)[:settings.CANDIDATE_POOL_TOP_K]
        except Exception as e:
            logger.error(f"Error fetching raw candidates: {e}")
            return []


class RetrieverBuilder:
    def __init__(self):
        logger.info("RetrieverBuilder initialized.")

    def build_hybrid_retriever(
        self,
        tenant_id: str = "default_tenant",
        thread_id: str = None,
        document_ids: List[str] = None,
        allowed_roles: List[str] = None,
        applicability: dict = None,
        domain: str = None,
        intent_type: str = None,
        target_date: str = None,
        docs=None,
        k: int = None,
        final_k: int = None,
        w_dense: float = 0.7,
        w_sparse: float = 0.3
    ):
        return QdrantHybridRetriever(
            k=k if k is not None else settings.VECTOR_SEARCH_K,
            final_k=final_k if final_k is not None else settings.RERANKER_TOP_K,
            threshold=settings.RERANKER_THRESHOLD,
            tenant_id=tenant_id,
            thread_id=thread_id,
            document_ids=document_ids,
            allowed_roles=allowed_roles,
            applicability=applicability,
            domain=domain,
            intent_type=intent_type,
            target_date=target_date,
            w_dense=w_dense,
            w_sparse=w_sparse
        )