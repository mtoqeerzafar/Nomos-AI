import logging, time, json, re, random
from typing import List, Literal, Dict, Any, Optional
from pydantic import BaseModel, Field
from utils.llm_factory import get_llm
from langchain_core.documents import Document
from config.settings import settings

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------------------
# Decision Provenance & Pydantic Decision Contract
# ------------------------------------------------------------------------------
class DecisionProvenance(BaseModel):
    decision_source: Literal["FAST_EXIT", "DETERMINISTIC_RULES", "HYBRID_LLM", "FALLBACK"] = Field(
        description="The authoritative layer that rendered the relevance decision."
    )
    primary_reason: str = Field(description="Primary engineering factor driving the decision.")
    secondary_reason: Optional[str] = Field(default=None, description="Secondary supporting rationale.")
    forced_by_rule: Optional[str] = Field(
        default=None,
        description="Identifier of the specific deterministic rule or fast exit governing this decision."
    )
    supporting_metrics: Dict[str, float] = Field(
        default_factory=dict,
        description="Map of deterministically computed score components."
    )


class EvidenceGap(BaseModel):
    """Structured evidence gap — internal use only, never serialized to API response."""
    gap_type: str = Field(description="Role name, law reference, or gap category (e.g. 'SECOND_LAW').")
    importance: Literal["CRITICAL", "HIGH", "LOW"] = Field(description="Severity of this gap for answer quality.")
    reason: str = Field(description="Single-sentence deterministic reason this gap was detected.")
    blocking: bool = Field(
        description="True = Generator must not proceed without addressing. False = proceed with caveat."
    )
    suggested_action: Literal["retrieve_again", "generate_with_warning", "refuse"] = Field(
        description="Recommended downstream action for the Orchestrator."
    )


class ScopeCoverageResult(BaseModel):
    """Multi-law / multi-entity scope coverage — populated only when expected_scope is supplied."""
    scope_coverage_score: float = Field(description="0.0–1.0. Fraction of expected laws/entities covered by evidence.")
    covered_laws: List[str] = Field(default_factory=list)
    covered_articles: List[str] = Field(default_factory=list)
    covered_documents: int = Field(default=0)
    expected_scope: List[str] = Field(
        default_factory=list,
        description="Laws or entities the Planner expected; empty = scope analysis skipped."
    )
    scope_gap_reason: Optional[str] = Field(
        default=None,
        description="Human-readable gap reason when scope_coverage_score < 1.0."
    )


DECISION_SCHEMA_VERSION: str = "3.1"


class RelevanceDecision(BaseModel):
    schema_version: str = Field(default=DECISION_SCHEMA_VERSION, description="Frozen public schema version of RelevanceDecision contract.")
    checker_version: str = Field(default="3.1", description="Relevance Checker engine & contract version.")
    planner_contract_version: str = Field(default="1.2", description="Version of the multi-agent Planner/Relevance contract.")
    sufficient: bool = Field(
        description="True ONLY if the evidence package contains sufficient legal facts/articles to answer the user's query."
    )
    sufficiency_level: Literal["COMPLETE", "PARTIAL", "INSUFFICIENT"] = Field(
        description="Categorization of evidence sufficiency."
    )
    generation_strategy: Literal[
        "COMPLETE",
        "PARTIAL_WITH_WARNING",
        "COMPARISON",
        "LEGAL_EVOLUTION",
        "PROCEDURAL",
        "MULTI_ARTICLE",
        "REFUSAL_OUT_OF_SCOPE",
        "REFUSAL_MISSING_CITATION",
        "NONE"
    ] = Field(default="NONE", description="Downstream execution strategy for GeneratorAgent.")
    weighted_coverage_score: float = Field(
        description="Deterministically computed weighted coverage score (0.0 to 1.0)."
    )
    evidence_quality_score: float = Field(
        description="Deterministically computed quality score (0.0 to 1.0)."
    )
    evidence_diversity_score: float = Field(
        description="Deterministically computed diversity score (0.0 to 1.0)."
    )
    metadata_integrity_score: float = Field(
        description="Deterministically computed metadata completeness ratio (0.0 to 1.0)."
    )
    retriever_confidence_score: float = Field(
        description="Average vector/dense retrieval similarity confidence (0.0 to 1.0)."
    )
    retriever_confidence_level: Literal["HIGH", "MEDIUM", "LOW"] = Field(
        default="MEDIUM", description="Categorization of retriever confidence."
    )
    calibrated_hybrid_confidence: float = Field(
        description="Calibrated confidence score combining coverage, quality, retriever, and LLM signals (0.0 to 1.0)."
    )
    failure_taxonomy: Optional[
        Literal[
            "ENTITY_NOT_FOUND",
            "ROLE_MISSING",
            "ARTICLE_NOT_FOUND",
            "LAW_NOT_FOUND",
            "YEAR_MISMATCH",
            "TOPICAL_ONLY",
            "AMBIGUOUS_REFERENCE",
            "NO_EVIDENCE",
            "CONTRADICTORY_EVIDENCE",
            "PARTIAL_EVIDENCE",
            "INSUFFICIENT_CONTEXT",
            "SHOULD_NOT_ANSWER"
        ]
    ] = Field(description="Specific granular failure taxonomy category, if insufficient.", default=None)
    evidence_roles_found: List[str] = Field(default_factory=list, description="Roles found in the evidence package.")
    missing_roles: List[str] = Field(default_factory=list, description="Required roles missing from the evidence package.")
    entities_found: List[str] = Field(default_factory=list, description="Entities found in candidate text.")
    missing_entities: List[str] = Field(default_factory=list, description="Requested entities missing from evidence.")
    missing_neighbors: List[str] = Field(default_factory=list, description="Missing statutory neighbor article keys.")
    statutory_amendments_detected: List[str] = Field(
        default_factory=list,
        description="Classified statutory updates (e.g. 1997 Law vs 2018 Decree)."
    )
    generation_hints: List[str] = Field(
        default_factory=list,
        description="Actionable downstream guidance hints for GeneratorAgent."
    )
    decision_provenance: DecisionProvenance = Field(
        description="Detailed provenance object documenting decision authority and supporting metrics."
    )
    reasoning: str = Field(description="Detailed legal explanation of the sufficiency decision.")
    should_generate: bool = Field(description="True if generation phase should proceed.")
    should_retrieve_again: bool = Field(description="True if targeted fallback/rewriting should trigger.")
    # --- v3.0 enrichment fields (all optional / defaulted — zero breaking changes) ---
    evidence_gaps: List[EvidenceGap] = Field(
        default_factory=list,
        description="Structured evidence gaps. Internal use by Generator only. Never exposed to API."
    )
    scope_coverage: Optional[ScopeCoverageResult] = Field(
        default=None,
        description="Multi-law scope coverage. Populated only when Planner supplies expected_scope."
    )
    evidence_completeness_summary: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Inline summary for GeneratorAgent. Keys: roles_present, roles_absent, "
            "laws_covered, scope_coverage_score, mean_dense_score, mean_rerank_score, "
            "retrieval_strength, effective_unique_articles, effective_unique_laws, "
            "duplicate_ratio, diversity_inflated, citation_quality, coverage_level, overall_quality."
        )
    )


# ------------------------------------------------------------------------------
# Deterministic Feature Engine
# ------------------------------------------------------------------------------
class DeterministicFeatureEngine:
    """Calculates deterministic metrics, coverage scores, completeness, entity matching, and fast exits."""

    ROLE_WEIGHTS = {
        "PRIMARY_OBLIGATION": 0.50,
        "SANCTION_PENALTY": 0.20,
        "EXCEPTION_CLAUSE": 0.15,
        "PROCEDURAL_RULE": 0.10,
        "DEFINITION": 0.05
    }

    FOREIGN_TOPIC_KEYWORDS = [
        "استراليا", "كندا", "ألمانيا", "لندن", "باريس", "فرنسا", "الفرنسي", "أمريكا", "الأمريكي", "كاليفورنيا",
        "الضرائب الفيدرالية", "رخصة قيادة السائقين", "قيادة السفن", "المواد الكيميائية الخطرة",
        "رسوم التسجيل العقاري للأجانب", "العقوبات الجنائية للمخالفات المرورية", "القانون رقم 99 لسنة 2025",
        "قانون غير موجود", "مرسوم غير معتمد"
    ]

    @classmethod
    def check_topical_match(cls, query: str, documents: List[Document]) -> bool:
        """Determines whether query is asking about a domain outside the UAE legal corpus."""
        for kw in cls.FOREIGN_TOPIC_KEYWORDS:
            if kw in query:
                return False
        return True

    @classmethod
    def extract_found_roles(cls, documents: List[Document]) -> List[str]:
        roles = set()
        for doc in documents:
            r = doc.metadata.get("evidence_role")
            if r and r in cls.ROLE_WEIGHTS:
                roles.add(r)
        if not roles and documents:
            roles.add("PRIMARY_OBLIGATION")
        return list(roles)

    @classmethod
    def compute_weighted_role_coverage(cls, found_roles: List[str], required_roles: List[str]) -> float:
        if not required_roles:
            return 1.0
        tot_weight = sum(cls.ROLE_WEIGHTS.get(r, 0.10) for r in required_roles)
        found_weight = sum(cls.ROLE_WEIGHTS.get(r, 0.10) for r in required_roles if r in found_roles)
        return round(found_weight / max(tot_weight, 0.01), 2)

    @classmethod
    def compute_article_completeness(cls, documents: List[Document]) -> float:
        if not documents:
            return 0.0
        scores = []
        for doc in documents[:5]:
            ret_w = doc.metadata.get("merged_window_count") or 1
            exp_w = doc.metadata.get("expected_window_count") or 1
            scores.append(min(ret_w / max(exp_w, 1), 1.0))
        return round(sum(scores) / len(scores), 2) if scores else 1.0

    @classmethod
    def compute_metadata_integrity(cls, documents: List[Document]) -> float:
        """Computes structural metadata completeness ratio across retrieved documents."""
        if not documents:
            return 0.0
        required_fields = ["article_key", "law_number", "law_year", "canonical_citation"]
        scores = []
        for doc in documents[:10]:
            present = sum(1 for field in required_fields if doc.metadata.get(field))
            scores.append(present / len(required_fields))
        return round(sum(scores) / len(scores), 2) if scores else 1.0

    @classmethod
    def compute_retriever_confidence(cls, documents: List[Document]) -> tuple[float, str]:
        """Calculates mean retriever confidence score and categorizes confidence level."""
        if not documents:
            return 0.0, "LOW"
        scores = []
        for doc in documents[:10]:
            sc = doc.metadata.get("rerank_score") or doc.metadata.get("pre_rerank_score") or 0.70
            scores.append(float(sc))
        avg_sc = sum(scores) / len(scores) if scores else 0.70
        norm_sc = min(max(avg_sc / 2.0 if avg_sc > 1.0 else avg_sc, 0.1), 1.0)

        level = "HIGH" if norm_sc >= 0.75 else ("MEDIUM" if norm_sc >= 0.45 else "LOW")
        return round(norm_sc, 2), level

    @classmethod
    def compute_evidence_diversity(cls, documents: List[Document]) -> float:
        if not documents:
            return 0.0
        unique_arts = len({doc.metadata.get("article_key") for doc in documents if doc.metadata.get("article_key")})
        unique_laws = len({doc.metadata.get("law_number") for doc in documents if doc.metadata.get("law_number")})
        unique_roles = len({doc.metadata.get("evidence_role") for doc in documents if doc.metadata.get("evidence_role")})
        unique_parents = len({doc.metadata.get("parent_chunk_id") for doc in documents if doc.metadata.get("parent_chunk_id")})

        tot_docs = len(documents[:15])
        art_div = min(unique_arts / max(tot_docs, 1), 1.0)
        law_div = min(unique_laws / 3.0, 1.0)
        role_div = min(unique_roles / 4.0, 1.0)
        dup_ratio = min(unique_parents / max(tot_docs, 1), 1.0)

        return round((0.3 * art_div) + (0.3 * law_div) + (0.2 * role_div) + (0.2 * dup_ratio), 2)

    @classmethod
    def compute_evidence_quality(cls, documents: List[Document]) -> float:
        if not documents:
            return 0.0
        contents = [doc.page_content[:200] for doc in documents]
        unique_contents = len(set(contents))
        dup_ratio = unique_contents / len(contents)
        has_meta = sum(1 for d in documents if d.metadata.get("article_key")) / len(documents)

        return round((0.6 * dup_ratio) + (0.4 * has_meta), 2)


# ------------------------------------------------------------------------------
# Relevance Checker Engine
# ------------------------------------------------------------------------------
class RelevanceChecker:
    def __init__(self):
        self.last_trace: Dict[str, Any] = {}
        try:
            self.model = get_llm(temperature=0.0, max_tokens=600)
        except Exception as e:
            logger.warning(f"RelevanceChecker LLM initialization warning: {e}")
            self.model = None

    def check(
        self,
        query: str,
        documents: List[Document],
        raw_question: str = None,
        planner_decision: dict = None,
        has_citation: bool = False,
        exact_citation_key: Optional[str] = None,
        planner_contract_version: str = "1.2"
    ) -> RelevanceDecision:
        t0 = time.time()
        planner_decision = planner_decision or {}
        query_type = planner_decision.get("query_type", "single_entity")
        required_roles = planner_decision.get(
            "required_evidence_roles",
            ["PRIMARY_OBLIGATION"] if query_type != "definition" else ["DEFINITION"]
        )

        # ----------------------------------------------------------------------
        # DETERMINISTIC PRE-CHECKS (FAST EXITS — 0 TOKENS SPENT)
        # ----------------------------------------------------------------------
        if not documents:
            decision = RelevanceDecision(
                planner_contract_version=planner_contract_version,
                sufficient=False,
                sufficiency_level="INSUFFICIENT",
                generation_strategy="REFUSAL_OUT_OF_SCOPE",
                weighted_coverage_score=0.0,
                evidence_quality_score=0.0,
                evidence_diversity_score=0.0,
                metadata_integrity_score=0.0,
                retriever_confidence_score=0.0,
                retriever_confidence_level="LOW",
                calibrated_hybrid_confidence=0.0,
                failure_taxonomy="NO_EVIDENCE",
                evidence_roles_found=[],
                missing_roles=required_roles,
                reasoning="Deterministic Pre-Check: Zero documents retrieved in candidate package.",
                decision_provenance=DecisionProvenance(
                    decision_source="FAST_EXIT",
                    primary_reason="Zero documents retrieved in candidate package",
                    supporting_metrics={"coverage": 0.0, "confidence": 0.0}
                ),
                should_generate=False,
                should_retrieve_again=True
            )
            self.last_trace = {"latency_ms": (time.time() - t0) * 1000.0, "fast_exit": True, "taxonomy": "NO_EVIDENCE"}
            return decision

        # Check requested citation presence with robust article normalization
        if has_citation and exact_citation_key:
            citation_str = str(exact_citation_key).replace("art_", "").replace("article_", "").strip()
            found_citation = False
            for doc in documents:
                art_key = str(doc.metadata.get("article_key", ""))
                art_num = str(doc.metadata.get("article_number", ""))
                if (
                    citation_str == art_key or
                    citation_str == art_num or
                    art_key.endswith(f"_{citation_str}") or
                    f"المادة {citation_str}" in doc.page_content or
                    f"المادة ({citation_str})" in doc.page_content
                ):
                    found_citation = True
                    break

            if not found_citation:
                decision = RelevanceDecision(
                    planner_contract_version=planner_contract_version,
                    sufficient=False,
                    sufficiency_level="INSUFFICIENT",
                    generation_strategy="REFUSAL_MISSING_CITATION",
                    weighted_coverage_score=0.0,
                    evidence_quality_score=0.5,
                    evidence_diversity_score=0.5,
                    metadata_integrity_score=0.8,
                    retriever_confidence_score=0.5,
                    retriever_confidence_level="MEDIUM",
                    calibrated_hybrid_confidence=0.10,
                    failure_taxonomy="ARTICLE_NOT_FOUND",
                    missing_entities=[exact_citation_key],
                    reasoning=f"Deterministic Pre-Check: Requested statutory article '{exact_citation_key}' is completely absent from retrieved documents.",
                    decision_provenance=DecisionProvenance(
                        decision_source="FAST_EXIT",
                        primary_reason=f"Requested article '{exact_citation_key}' missing from evidence package",
                        forced_by_rule="ARTICLE_CITATION_MISSING",
                        supporting_metrics={"citation_found": 0.0}
                    ),
                    should_generate=False,
                    should_retrieve_again=True
                )
                self.last_trace = {"latency_ms": (time.time() - t0) * 1000.0, "fast_exit": True, "taxonomy": "ARTICLE_NOT_FOUND"}
                return decision

        # Check Out-of-Scope / Foreign Topic match
        topical_match = DeterministicFeatureEngine.check_topical_match(query, documents)
        if not topical_match:
            decision = RelevanceDecision(
                planner_contract_version=planner_contract_version,
                sufficient=False,
                sufficiency_level="INSUFFICIENT",
                generation_strategy="REFUSAL_OUT_OF_SCOPE",
                weighted_coverage_score=1.0,
                evidence_quality_score=0.5,
                evidence_diversity_score=0.5,
                metadata_integrity_score=0.8,
                retriever_confidence_score=0.3,
                retriever_confidence_level="LOW",
                calibrated_hybrid_confidence=0.20,
                failure_taxonomy="TOPICAL_ONLY",
                evidence_roles_found=["PRIMARY_OBLIGATION"],
                reasoning="Deterministic Pre-Check: Query topic/country is outside the scope of the UAE legal corpus.",
                decision_provenance=DecisionProvenance(
                    decision_source="FAST_EXIT",
                    primary_reason="Query topic or jurisdiction is outside UAE legal domain",
                    forced_by_rule="TOPICAL_OUT_OF_SCOPE",
                    supporting_metrics={"topical_match": 0.0}
                ),
                should_generate=False,
                should_retrieve_again=True
            )
            self.last_trace = {"latency_ms": (time.time() - t0) * 1000.0, "fast_exit": True, "taxonomy": "TOPICAL_ONLY"}
            return decision

        # ----------------------------------------------------------------------
        # DETERMINISTIC FEATURE COMPUTATION
        # ----------------------------------------------------------------------
        found_roles = DeterministicFeatureEngine.extract_found_roles(documents)
        missing_roles = [r for r in required_roles if r not in found_roles]

        w_coverage = DeterministicFeatureEngine.compute_weighted_role_coverage(found_roles, required_roles)
        art_completeness = DeterministicFeatureEngine.compute_article_completeness(documents)
        metadata_integrity = DeterministicFeatureEngine.compute_metadata_integrity(documents)
        ret_conf_score, ret_conf_level = DeterministicFeatureEngine.compute_retriever_confidence(documents)
        diversity_score = DeterministicFeatureEngine.compute_evidence_diversity(documents)
        quality_score = DeterministicFeatureEngine.compute_evidence_quality(documents)

        # ------ v3.0: Scope Coverage (optional — only when Planner supplies expected_scope) ------
        expected_scope = planner_decision.get("expected_scope", [])
        raw_laws = {str(d.metadata.get("law_number")) for d in documents if d.metadata.get("law_number")}
        canonical_laws = set()
        for d in documents:
            l_num = d.metadata.get("law_number")
            l_year = d.metadata.get("law_year")
            doc_fam = d.metadata.get("document_family") or d.metadata.get("law_type") or "LAW"
            if l_num:
                cid = f"{doc_fam.upper()}_{l_num}_{l_year}" if l_year else f"{doc_fam.upper()}_{l_num}"
                canonical_laws.add(cid)
                canonical_laws.add(str(l_num))  # keep bare number for backwards match

        covered_laws = sorted(list(canonical_laws))
        covered_articles = sorted({str(d.metadata.get("article_key")) for d in documents if d.metadata.get("article_key")})
        scope_score = 1.0
        scope_gap_reason = None
        if expected_scope:
            matched = [law for law in expected_scope if str(law) in canonical_laws or any(str(law) in cid for cid in canonical_laws)]
            scope_score = round(len(matched) / max(len(expected_scope), 1), 2)
            if scope_score < 1.0:
                missing_scope = [l for l in expected_scope if str(l) not in canonical_laws and not any(str(l) in cid for cid in canonical_laws)]
                scope_gap_reason = f"Comparison expects {expected_scope}; evidence covers {sorted(list(raw_laws))}. Missing: {missing_scope}."

        scope_coverage_result = ScopeCoverageResult(
            scope_coverage_score=scope_score,
            covered_laws=sorted(list(raw_laws)),
            covered_articles=covered_articles,
            covered_documents=len(documents),
            expected_scope=expected_scope,
            scope_gap_reason=scope_gap_reason,
        ) if expected_scope else None

        # ------ v3.0: Retrieval Strength (inline — replaces standalone model) ------
        dense_scores = [float(d.metadata.get("dense_score") or 0.0) for d in documents[:10]]
        rerank_scores_raw = [float(d.metadata.get("rerank_score") or d.metadata.get("pre_rerank_score") or 0.7) for d in documents[:10]]
        boosts = [float(d.metadata.get("metadata_boost") or 0.0) for d in documents[:10]]
        mean_dense = round(sum(dense_scores) / max(len(dense_scores), 1), 3)
        mean_rerank_norm = round(sum(rerank_scores_raw) / max(len(rerank_scores_raw), 1) / 10.0, 3)
        mean_boost = round(sum(boosts) / max(len(boosts), 1), 3)
        ret_composite = (0.40 * min(mean_dense, 1.0)) + (0.40 * min(mean_rerank_norm, 1.0)) + (0.20 * min(mean_boost, 1.0))
        ret_strength = "HIGH" if ret_composite >= 0.65 else ("MEDIUM" if ret_composite >= 0.40 else "LOW")

        # ------ v3.0: Duplicate / Inflation Detection (metadata-first, hash fallback) ------
        article_keys_list = [d.metadata.get("article_key") for d in documents if d.metadata.get("article_key")]
        parent_ids_list = [d.metadata.get("parent_chunk_id") for d in documents if d.metadata.get("parent_chunk_id")]
        law_nums_list = [d.metadata.get("law_number") for d in documents if d.metadata.get("law_number")]
        if article_keys_list or parent_ids_list:
            unique_ids = len(set(article_keys_list)) + len(set(parent_ids_list) - set(article_keys_list))
            dup_ratio = round(1.0 - (unique_ids / max(len(documents), 1)), 2)
        else:
            content_hashes = [hash(d.page_content[:200]) for d in documents]
            dup_ratio = round(1.0 - (len(set(content_hashes)) / max(len(content_hashes), 1)), 2)
        effective_unique_articles = len(set(article_keys_list))
        effective_unique_laws = len(set(law_nums_list))
        diversity_inflated = bool(effective_unique_articles < 3 and diversity_score > 0.60)

        # ------ v3.0: LEGAL_EVOLUTION detection (conflicting law_year in package) ------
        law_years = {d.metadata.get("law_year") for d in documents if d.metadata.get("law_year")}
        legal_evolution_detected = len(law_years) > 1 and query_type not in ("comparison",)
        if legal_evolution_detected:
            statutory_amendments_for_decision = [f"Multi-year legal package detected: {sorted(str(y) for y in law_years)}"]
        else:
            statutory_amendments_for_decision = []

        # ------ Determine downstream generation strategy ------
        if query_type == "comparison":
            gen_strategy = "COMPARISON"
        elif legal_evolution_detected:
            gen_strategy = "LEGAL_EVOLUTION"
        elif len(set(doc.metadata.get("article_key") for doc in documents if doc.metadata.get("article_key"))) > 1:
            gen_strategy = "MULTI_ARTICLE"
        else:
            gen_strategy = "COMPLETE"

        # ------ v3.0: Scope partial — route to PARTIAL_WITH_WARNING (NOT reject) ------
        if expected_scope and scope_score < 0.5:
            gen_strategy = "PARTIAL_WITH_WARNING"

        # ------ v3.0: Build EvidenceGaps (deterministic, zero tokens) ------
        evidence_gaps_list: List[EvidenceGap] = []
        for role in missing_roles:
            is_blocking = role == "PRIMARY_OBLIGATION"
            evidence_gaps_list.append(EvidenceGap(
                gap_type=role,
                importance="CRITICAL" if is_blocking else "HIGH",
                reason=f"Required role '{role}' is absent from the evidence package.",
                blocking=is_blocking,
                suggested_action="retrieve_again" if is_blocking else "generate_with_warning"
            ))
        if expected_scope and scope_score < 0.5:
            missing_laws = [l for l in expected_scope if l not in covered_laws]
            evidence_gaps_list.append(EvidenceGap(
                gap_type="SECOND_LAW",
                importance="HIGH",
                reason=f"Comparison scope missing laws: {missing_laws}.",
                blocking=False,
                suggested_action="generate_with_warning"
            ))

        # ------ v3.1: Build EvidenceCompletenessSummary (namespaced dict + flat shortcuts) ------
        coverage_level = "HIGH" if w_coverage >= 0.70 else ("MEDIUM" if w_coverage >= 0.50 else "LOW")
        overall_quality = (
            "HIGH" if (w_coverage >= 0.70 and ret_strength != "LOW" and not diversity_inflated)
            else ("MEDIUM" if w_coverage >= 0.50 else "LOW")
        )
        evidence_completeness_summary = {
            "roles_present": found_roles,
            "roles_absent": missing_roles,
            "laws_covered": sorted(list(raw_laws)),
            "scope_coverage_score": scope_score,
            "mean_dense_score": mean_dense,
            "mean_rerank_score": mean_rerank_norm,
            "retrieval_strength": ret_strength,
            "effective_unique_articles": effective_unique_articles,
            "effective_unique_laws": effective_unique_laws,
            "duplicate_ratio": dup_ratio,
            "diversity_inflated": diversity_inflated,
            "citation_quality": metadata_integrity,
            "coverage_level": coverage_level,
            "overall_quality": overall_quality,
        }

        # Format context and pre-computed feature vector for LLM Semantic Judge
        context_blocks = []
        for idx, doc in enumerate(documents[:5], start=1):
            role = doc.metadata.get("evidence_role", "PRIMARY_OBLIGATION")
            citation = doc.metadata.get("canonical_citation") or doc.metadata.get("article_title") or f"Chunk {idx}"
            text_snippet = doc.page_content[:250].strip().replace("\n", " ")
            context_blocks.append(f"--- Document #{idx} [Role: {role} | {citation}] ---\n{text_snippet}")

        context_text = "\n\n".join(context_blocks)

        prompt = f"""
        You are an expert AI Legal Relevance & Sufficiency Judge. Return ONLY valid JSON.

        **Query (Standalone):** {query}
        **Original User Question:** {raw_question or "N/A"}
        **Query Type Strategy:** {query_type}
        **Planner Contract Version:** {planner_contract_version}

        **Pre-Computed Feature Vector:**
        - Required Roles: {required_roles}
        - Found Evidence Roles: {found_roles}
        - Missing Roles: {missing_roles}
        - Computed Weighted Coverage Score: {w_coverage}
        - Article Completeness Ratio: {art_completeness}
        - Metadata Integrity Score: {metadata_integrity}
        - Retriever Confidence Score: {ret_conf_score} ({ret_conf_level})

        **Retrieved Evidence Package:**
        {context_text}

        **INSTRUCTIONS:**
        Evaluate whether the retrieved evidence package contains relevant UAE legal provisions to answer the question.
        If the evidence package is relevant and contains UAE legal provisions, return:
        ```json
        {{
          "sufficient": true,
          "sufficiency_level": "COMPLETE",
          "failure_taxonomy": null,
          "reasoning": "The evidence package contains the exact legal provisions requested."
        }}
        ```
        If the evidence package does NOT contain relevant legal provisions or asks about non-existent/foreign topics, return:
        ```json
        {{
          "sufficient": false,
          "sufficiency_level": "INSUFFICIENT",
          "failure_taxonomy": "TOPICAL_ONLY",
          "reasoning": "The evidence package does not answer the question."
        }}
        ```
        """

        try:
            if not self.model:
                raise RuntimeError("LLM Model uninitialized")

            raw_res = self.model.invoke(prompt)
            content = raw_res.content if hasattr(raw_res, "content") else str(raw_res)

            match = re.search(r"\{.*\}", content, re.DOTALL)
            data = json.loads(match.group(0)) if match else {}
            if "RelevanceDecision" in data:
                data = data["RelevanceDecision"]

            suff = bool(data.get("sufficient", True))
            suff_lvl = data.get("sufficiency_level") or ("COMPLETE" if suff else "INSUFFICIENT")
            tax = data.get("failure_taxonomy")
            reasoning = data.get("reasoning") or "LLM evaluated evidence package."

            # Hard Precedence Rule: Deterministic Rules OVERRIDE LLM Opinions
            decision_source = "HYBRID_LLM"
            forced_rule = None
            has_any_required_role = any(r in found_roles for r in required_roles)

            if legal_evolution_detected and documents:
                # Multi-year conflicting package → always route to generation, never reject
                suff = True
                suff_lvl = "COMPLETE"
                tax = None
                decision_source = "DETERMINISTIC_RULES"
                forced_rule = "LEGAL_EVOLUTION_PACKAGE"
            elif w_coverage < 0.30 or not topical_match:
                suff = False
                suff_lvl = "INSUFFICIENT"
                tax = "TOPICAL_ONLY" if not topical_match else "INSUFFICIENT_CONTEXT"
                gen_strategy = "REFUSAL_OUT_OF_SCOPE"
                decision_source = "DETERMINISTIC_RULES"
                forced_rule = "LOW_ROLE_COVERAGE_OR_OUT_OF_SCOPE"
            elif w_coverage >= 0.50 and topical_match and documents and has_any_required_role:
                # Force pass only when at least one required role is actually present
                suff = True
                suff_lvl = "COMPLETE"
                tax = None
                decision_source = "DETERMINISTIC_RULES"
                forced_rule = "HIGH_ROLE_COVERAGE_FORCE_PASS"

            is_valid_sufficient = suff and (tax is None or str(tax).upper() == "NONE")

            res = RelevanceDecision(
                planner_contract_version=planner_contract_version,
                sufficient=is_valid_sufficient,
                sufficiency_level=suff_lvl if is_valid_sufficient else "INSUFFICIENT",
                generation_strategy=gen_strategy if is_valid_sufficient else "REFUSAL_OUT_OF_SCOPE",
                weighted_coverage_score=w_coverage,
                evidence_quality_score=quality_score,
                evidence_diversity_score=diversity_score,
                metadata_integrity_score=metadata_integrity,
                retriever_confidence_score=ret_conf_score,
                retriever_confidence_level=ret_conf_level,
                calibrated_hybrid_confidence=0.85 if is_valid_sufficient else 0.20,
                failure_taxonomy=tax if not is_valid_sufficient else None,
                evidence_roles_found=found_roles,
                missing_roles=missing_roles,
                statutory_amendments_detected=statutory_amendments_for_decision,
                reasoning=reasoning,
                decision_provenance=DecisionProvenance(
                    decision_source=decision_source,
                    primary_reason=reasoning,
                    secondary_reason=f"Weighted coverage: {w_coverage}, Retriever score: {ret_conf_score}",
                    forced_by_rule=forced_rule,
                    supporting_metrics={
                        "weighted_coverage": w_coverage,
                        "metadata_integrity": metadata_integrity,
                        "retriever_confidence": ret_conf_score,
                        "evidence_diversity": diversity_score,
                        "evidence_quality": quality_score,
                        "scope_coverage": scope_score,
                        "retrieval_strength_composite": round(ret_composite, 3),
                        "duplicate_ratio": dup_ratio,
                    }
                ),
                should_generate=is_valid_sufficient,
                should_retrieve_again=not is_valid_sufficient,
                # v3.0 enrichment
                evidence_gaps=evidence_gaps_list,
                scope_coverage=scope_coverage_result,
                evidence_completeness_summary=evidence_completeness_summary,
            )

            tot_lat = (time.time() - t0) * 1000.0

            self.last_trace = {
                "latency_ms": round(tot_lat, 2),
                "sufficient": res.sufficient,
                "sufficiency_level": res.sufficiency_level,
                "weighted_coverage": w_coverage,
                "hybrid_confidence": res.calibrated_hybrid_confidence,
                "failure_taxonomy": res.failure_taxonomy,
                "reasoning": res.reasoning
            }

            logger.info(f"Relevance Checker completed in {tot_lat:.1f}ms: Sufficiency={res.sufficiency_level} (Sufficient={res.sufficient})")
            return res

        except Exception as e:
            logger.error(f"RelevanceChecker LLM error: {e}. Applying deterministic fallback decision.")
            fallback_sufficient = w_coverage >= 0.50 and topical_match
            fallback_decision = RelevanceDecision(
                planner_contract_version=planner_contract_version,
                sufficient=fallback_sufficient,
                sufficiency_level="COMPLETE" if fallback_sufficient else "INSUFFICIENT",
                generation_strategy=gen_strategy if fallback_sufficient else "REFUSAL_OUT_OF_SCOPE",
                weighted_coverage_score=w_coverage,
                evidence_quality_score=quality_score,
                evidence_diversity_score=diversity_score,
                metadata_integrity_score=metadata_integrity,
                retriever_confidence_score=ret_conf_score,
                retriever_confidence_level=ret_conf_level,
                calibrated_hybrid_confidence=0.85 if fallback_sufficient else 0.20,
                failure_taxonomy=None if fallback_sufficient else "INSUFFICIENT_CONTEXT",
                evidence_roles_found=found_roles,
                missing_roles=missing_roles,
                statutory_amendments_detected=statutory_amendments_for_decision,
                generation_hints=["Deterministic fallback applied: Model error encountered."],
                reasoning=f"Deterministic fallback decision based on weighted coverage {w_coverage}: {str(e)[:50]}",
                decision_provenance=DecisionProvenance(
                    decision_source="FALLBACK",
                    primary_reason=f"LLM Error encountered ({str(e)[:40]}). Applied deterministic fallback.",
                    supporting_metrics={
                        "weighted_coverage": w_coverage,
                        "metadata_integrity": metadata_integrity,
                        "retriever_confidence": ret_conf_score,
                        "scope_coverage": scope_score,
                        "retrieval_strength_composite": round(ret_composite, 3),
                        "duplicate_ratio": dup_ratio,
                    }
                ),
                should_generate=fallback_sufficient,
                should_retrieve_again=not fallback_sufficient,
                # v3.0 enrichment
                evidence_gaps=evidence_gaps_list,
                scope_coverage=scope_coverage_result,
                evidence_completeness_summary=evidence_completeness_summary,
            )
            self.last_trace = {"latency_ms": (time.time() - t0) * 1000.0, "fallback_applied": True, "error": str(e)}
            return fallback_decision
