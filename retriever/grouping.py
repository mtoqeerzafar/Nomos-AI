"""
Grouping Engine for Candidate Reduction & Evidence Preservation (Phase 2)
Positioned between Multi-Provider Search (Top 75) and Reranker/Context Assembly (Top 15-20).
"""

import time
from typing import List, Dict, Any, Set, Tuple
from dataclasses import dataclass
from loguru import logger


@dataclass
class GroupedCandidate:
    """Represents a grouped legal context block containing unified chunk text."""
    group_id: str
    score: float
    document_text: str
    metadata: Dict[str, Any]
    source_chunk_ids: List[str]
    source_provenance: str
    article_key: str


class CandidateGrouper:
    """
    Candidate Grouping Engine
    Performs sliding-window merging, parent/child aggregation, duplicate purging,
    and score preservation without losing evidence.
    """

    def __init__(
        self,
        score_aggregation: str = "max",
        similarity_threshold: float = 0.92,
        max_merged_length: int = 4000
    ):
        self.score_aggregation = score_aggregation.lower()
        self.similarity_threshold = similarity_threshold
        self.max_merged_length = max_merged_length

    def group_candidates(
        self,
        candidates: List[Any],
        target_k: int = 15
    ) -> Tuple[List[GroupedCandidate], Dict[str, Any]]:
        t0 = time.time()
        initial_count = len(candidates)

        if not candidates:
            return [], {
                "initial_count": 0,
                "grouped_count": 0,
                "duplicates_removed": 0,
                "windows_merged": 0,
                "latency_ms": 0.0
            }

        # Step 1: Consolidate sliding-window splits belonging to the SAME article_key
        article_groups, windows_merged = self._group_by_article_key(candidates)

        # Step 2: Semantic Deduplication (purge true duplicate text blocks)
        deduped_groups, duplicates_removed = self._deduplicate_fast(article_groups)

        # Step 3: Sort by aggregated score descending and return target_k
        deduped_groups.sort(key=lambda g: g.score, reverse=True)
        final_groups = deduped_groups[:target_k]

        latency_ms = (time.time() - t0) * 1000.0

        metrics = {
            "initial_count": initial_count,
            "grouped_count": len(final_groups),
            "duplicates_removed": duplicates_removed,
            "windows_merged": windows_merged,
            "latency_ms": round(latency_ms, 2)
        }

        return final_groups, metrics

    def _group_by_article_key(self, candidates: List[Any]) -> Tuple[List[GroupedCandidate], int]:
        """Groups candidate chunks sharing the exact same article_key into a single unified article context block."""
        groups_map: Dict[str, List[Any]] = {}
        windows_merged = 0

        # Preserve order of appearance
        ordered_keys = []
        for cand in candidates:
            art_key = cand.metadata.get("article_key") or getattr(cand, "id", str(hash(cand.document)))
            if art_key not in groups_map:
                groups_map[art_key] = []
                ordered_keys.append(art_key)
            groups_map[art_key].append(cand)

        grouped_list: List[GroupedCandidate] = []

        for art_key in ordered_keys:
            cand_list = groups_map[art_key]
            if len(cand_list) > 1:
                windows_merged += (len(cand_list) - 1)
                cand_list.sort(key=lambda c: c.metadata.get("chunk_window_index", 0))

            # Highest score preserved + multi-chunk boost
            scores = [c.score for c in cand_list]
            agg_score = max(scores) + (0.02 * (len(scores) - 1))

            # Combine window texts
            merged_texts = []
            seen_texts = set()
            for c in cand_list:
                t_clean = c.document.strip()
                if t_clean and t_clean not in seen_texts:
                    seen_texts.add(t_clean)
                    merged_texts.append(t_clean)

            unified_text = "\n---\n".join(merged_texts)[:self.max_merged_length]
            base_meta = dict(cand_list[0].metadata)
            base_meta["merged_window_count"] = len(cand_list)

            sources = list({getattr(c, "source", "DENSE").value if hasattr(getattr(c, "source", None), "value") else str(getattr(c, "source", "DENSE")) for c in cand_list})

            grouped_list.append(GroupedCandidate(
                group_id=f"group_{art_key}",
                score=agg_score,
                document_text=unified_text,
                metadata=base_meta,
                source_chunk_ids=[getattr(c, "id", "") for c in cand_list],
                source_provenance="+".join(sources),
                article_key=art_key
            ))

        return grouped_list, windows_merged

    def _deduplicate_fast(self, groups: List[GroupedCandidate]) -> Tuple[List[GroupedCandidate], int]:
        """Fast token-set deduplication purging exact and 95%+ identical chunks."""
        unique_groups: List[GroupedCandidate] = []
        duplicates_removed = 0

        for g in groups:
            is_dup = False
            g_words = set(g.document_text.split()[:100])  # Check first 100 words for speed

            for ug in unique_groups:
                ug_words = set(ug.document_text.split()[:100])
                if not g_words or not ug_words:
                    continue

                inter = len(g_words.intersection(ug_words))
                union = len(g_words.union(ug_words))
                jaccard = inter / union if union > 0 else 0.0

                if jaccard >= self.similarity_threshold:
                    is_dup = True
                    duplicates_removed += 1
                    if g.score > ug.score:
                        ug.score = g.score
                        ug.document_text = g.document_text
                    break

            if not is_dup:
                unique_groups.append(g)

        return unique_groups, duplicates_removed

