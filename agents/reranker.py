import logging, time, json, re
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from langchain_core.documents import Document
from utils.llm_factory import get_llm
from config.settings import settings

logger = logging.getLogger(__name__)


class CandidateEvidenceRole(BaseModel):
    candidate_index: int = Field(description="The index (0 to N-1) of the chunk being evaluated")
    evidence_role: str = Field(
        description="One of: 'PRIMARY_OBLIGATION', 'SUPPORTING_CONTEXT', 'EXCEPTION_CLAUSE', 'SANCTION_PENALTY', 'PROCEDURAL_RULE', 'DEFINITION', 'IRRELEVANT'"
    )
    relevance_score: float = Field(description="Score from 0.0 to 10.0 indicating how essential this chunk is for a complete downstream legal answer")
    reason: str = Field(description="Brief legal rationale for placing this chunk at its assigned rank")
    confidence: float = Field(default=0.9, description="Confidence score from 0.0 to 1.0")


class EvidenceSetRerankResult(BaseModel):
    selected_order: List[CandidateEvidenceRole] = Field(
        description="Ordered list of candidate evaluations from most essential to least essential, forming a complete legal evidence package."
    )


class RerankerAgent:
    def __init__(self):
        self.last_trace: Dict[str, Any] = {}
        try:
            self.model = get_llm(temperature=0.0, max_tokens=1000)
        except Exception as e:
            logger.warning(f"RerankerAgent LLM initialization warning: {e}")
            self.model = None

    def rerank(
        self,
        query: str,
        documents: List[Document],
        top_k: int = settings.RERANKER_TOP_K,
        has_citation: bool = False,
        exact_citation_key: Optional[str] = None
    ) -> List[Document]:
        t0 = time.time()
        if not documents:
            self.last_trace = {"latency_ms": 0.0, "reranked_count": 0}
            return []

        if not self.model or len(documents) <= 1:
            for rank_idx, doc in enumerate(documents[:top_k], start=1):
                doc.metadata["rerank_score"] = doc.metadata.get("pre_rerank_score", 1.0)
                doc.metadata["rerank_reason"] = "Pre-Rerank Order Preserved (Fallback / Single Candidate)"
                doc.metadata["evidence_role"] = "PRIMARY_OBLIGATION" if rank_idx == 1 else "SUPPORTING_CONTEXT"
                doc.metadata["rerank_confidence"] = 1.0
                doc.metadata["citation_shield_used"] = False
                doc.metadata["pre_rerank_position"] = rank_idx
                doc.metadata["post_rerank_position"] = rank_idx

            self.last_trace = {
                "latency_ms": (time.time() - t0) * 1000.0,
                "reranked_count": len(documents[:top_k]),
                "citation_shield_used": False,
                "fallback_applied": True
            }
            return documents[:top_k]

        logger.info(f"Reranking {len(documents)} chunks for query: '{query}'")

        pinned_doc: Optional[Document] = None
        eval_docs = list(documents[:10])
        citation_shield_active = False

        if has_citation and exact_citation_key and len(documents) > 0:
            top_key = documents[0].metadata.get("article_key")
            if top_key == exact_citation_key:
                pinned_doc = documents[0]
                pinned_doc.metadata["rerank_score"] = 10.0
                pinned_doc.metadata["rerank_reason"] = "Citation Shield: Exact Statutory Match Pinned at Rank 1"
                pinned_doc.metadata["evidence_role"] = "PRIMARY_OBLIGATION"
                pinned_doc.metadata["rerank_confidence"] = 1.0
                pinned_doc.metadata["citation_shield_used"] = True
                pinned_doc.metadata["pre_rerank_position"] = 1
                pinned_doc.metadata["post_rerank_position"] = 1
                eval_docs = documents[1:10]
                citation_shield_active = True

        if not eval_docs:
            result_docs = [pinned_doc] if pinned_doc else []
            self.last_trace = {
                "latency_ms": (time.time() - t0) * 1000.0,
                "reranked_count": len(result_docs),
                "citation_shield_used": True
            }
            return result_docs

        docs_text = ""
        for idx, doc in enumerate(eval_docs):
            text_snippet = doc.page_content[:300].strip().replace("\n", " ")
            citation = doc.metadata.get("canonical_citation") or doc.metadata.get("article_title") or f"Chunk {idx}"
            art_key = doc.metadata.get("article_key", "N/A")
            docs_text += f"\n--- Candidate [{idx}] (Key: {art_key} | {citation}) ---\n{text_snippet}\n"

        prompt = f"""
        You are an expert Arabic legal search reranker. Return ONLY valid JSON.

        **User Query:** {query}

        **Candidates Available:**
        {docs_text}

        Return a JSON object with `selected_order`:
        ```json
        {{
          "selected_order": [
            {{
              "candidate_index": 0,
              "evidence_role": "PRIMARY_OBLIGATION",
              "relevance_score": 9.5,
              "reason": "Direct statutory match",
              "confidence": 0.95
            }}
          ]
        }}
        ```
        """

        try:
            raw_res = self.model.invoke(prompt)
            content = raw_res.content if hasattr(raw_res, "content") else str(raw_res)

            match = re.search(r"\{.*\}", content, re.DOTALL)
            data = json.loads(match.group(0)) if match else {}
            if "EvidenceSetRerankResult" in data:
                data = data["EvidenceSetRerankResult"]

            order = data.get("selected_order", []) or data.get("reranked_candidates", [])

            seen_indices = set()
            ordered_candidates = []

            for item in order:
                if isinstance(item, dict):
                    idx = item.get("candidate_index", 0)
                    role = item.get("evidence_role") or item.get("category") or "SUPPORTING_CONTEXT"
                    score = float(item.get("relevance_score") or item.get("score") or 5.0)
                    reason = item.get("reason") or "LLM Reranked"
                    conf = float(item.get("confidence") or 0.8)
                else:
                    idx = 0
                    role = "SUPPORTING_CONTEXT"
                    score = 5.0
                    reason = "LLM Reranked"
                    conf = 0.8

                if 0 <= idx < len(eval_docs) and idx not in seen_indices:
                    seen_indices.add(idx)
                    ordered_candidates.append({
                        "doc": eval_docs[idx],
                        "role": role,
                        "score": round(score, 2),
                        "reason": reason,
                        "confidence": round(conf, 2),
                        "orig_idx": idx
                    })

            for idx, doc in enumerate(documents):
                if idx not in seen_indices and doc != pinned_doc:
                    ordered_candidates.append({
                        "doc": doc,
                        "role": "SUPPORTING_CONTEXT",
                        "score": round(5.0 - (idx * 0.1), 2),
                        "reason": "Preserved original retrieval order (Unscored by LLM)",
                        "confidence": 0.5,
                        "orig_idx": idx
                    })

            final_docs = []
            if pinned_doc:
                final_docs.append(pinned_doc)

            needed_unpinned = top_k - len(final_docs)
            for post_idx, item in enumerate(ordered_candidates[:needed_unpinned], start=len(final_docs) + 1):
                doc = item["doc"]
                pre_pos = item["orig_idx"] + (2 if citation_shield_active else 1)

                doc.metadata["rerank_score"] = item["score"]
                doc.metadata["evidence_role"] = item["role"]
                doc.metadata["rerank_reason"] = item["reason"]
                doc.metadata["rerank_confidence"] = item["confidence"]
                doc.metadata["citation_shield_used"] = citation_shield_active
                doc.metadata["pre_rerank_position"] = pre_pos
                doc.metadata["post_rerank_position"] = post_idx
                final_docs.append(doc)

            tot_lat = (time.time() - t0) * 1000.0

            self.last_trace = {
                "latency_ms": round(tot_lat, 2),
                "total_candidates": len(documents),
                "reranked_count": len(final_docs),
                "citation_shield_used": citation_shield_active
            }

            logger.info(f"Evidence-Set Reranking successful in {tot_lat:.1f}ms. Top 1: {final_docs[0].metadata.get('article_key')}")
            return final_docs

        except Exception as e:
            logger.error(f"Reranking failed: {e}. Falling back to pre-rerank order.")
            fallback_docs = documents[:top_k]
            for r_idx, doc in enumerate(fallback_docs, start=1):
                doc.metadata["rerank_score"] = doc.metadata.get("pre_rerank_score", 1.0)
                doc.metadata["evidence_role"] = "PRIMARY_OBLIGATION" if r_idx == 1 else "SUPPORTING_CONTEXT"
                doc.metadata["rerank_reason"] = f"Fallback error: {str(e)[:50]}"
                doc.metadata["rerank_confidence"] = 0.5
                doc.metadata["citation_shield_used"] = False
                doc.metadata["pre_rerank_position"] = r_idx
                doc.metadata["post_rerank_position"] = r_idx

            self.last_trace = {
                "latency_ms": (time.time() - t0) * 1000.0,
                "reranked_count": len(fallback_docs),
                "citation_shield_used": False,
                "fallback_applied": True,
                "error": str(e)
            }
            return fallback_docs
