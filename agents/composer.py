"""
Phase 8 -- Response Composer v1.0  (Production Frozen)
=======================================================
Seven deterministic sub-engines: GenerationArtifacts -> ResponseOutput v1.0.
NO LLM. ZERO external calls. Pure presentation engineering.

Engine 0  ContractValidator  -- pre-composition internal consistency gate
Engine 1  ResponseSelector   -- routes on VerificationStatus
Engine 2  AnswerBuilder      -- selects body; RTL safety; markdown normalisation; splitting
Engine 3  CitationComposer   -- dedup, group, collapse, format citation keys -> Arabic
Engine 4  WarningComposer    -- gap taxonomy + claim status -> severity-ordered Arabic warnings
Engine 5  MetadataComposer   -- assembles full ResponseMetadata from all upstream contracts
Engine 6  OutputFormatter    -- renders for API/Markdown/Plain/Streaming/Teams/Slack

Public contract: ResponseOutput v1.0
No downstream consumer (API, UI, tests) should access GeneratorOutput,
VerificationResult, or GenerationArtifacts directly.
"""

import re
import hashlib
import time
import logging
from typing import List, Optional, Literal

from pydantic import BaseModel, Field

from agents.generator import GeneratorOutput, EvidenceReasoningGraph
from agents.verifier  import VerificationResult

logger = logging.getLogger(__name__)


# ============================================================================
# CONSTANTS
# ============================================================================

COMPOSER_CONTRACT_VERSION = "1.0"
CITATION_SCHEMA_VERSION   = "1.0"
WARNING_SCHEMA_VERSION    = "1.0"
METADATA_SCHEMA_VERSION   = "1.0"

MAX_ANSWER_CHARS = 6000   # chars; trigger deterministic splitting above this
MAX_CLAIM_DRIFT  = 5      # acceptable delta between generator/verifier claim counts


# ============================================================================
# ENUMS & TAXONOMIES
# ============================================================================

PresentationProfile = Literal[
    "LEGAL_STANDARD", "LEGAL_DETAILED",
    "SUMMARY", "PROCEDURAL", "TIMELINE", "COMPARISON",
]
OutputChannel = Literal[
    "API", "MARKDOWN", "PLAIN_TEXT",
    "STREAMING", "TEAMS", "SLACK", "WHATSAPP", "VOICE",
]
WarningSeverity = Literal["INFO", "WARNING", "CRITICAL", "BLOCKING"]
ResponseStatus  = Literal["ANSWER", "PARTIAL_ANSWER", "REFUSAL"]
SourceType      = Literal[
    "FEDERAL_LAW", "CABINET_RESOLUTION", "EXECUTIVE_REGULATION",
    "MINISTERIAL_RESOLUTION", "CIRCULAR", "GUIDELINE", "UNKNOWN",
]


# ============================================================================
# FROZEN DATA CONTRACTS (v1.0)
# ============================================================================

class Citation(BaseModel):
    """Frozen Citation contract v1.0."""
    schema_version:  str           = CITATION_SCHEMA_VERSION
    citation_key:    str
    law_title:       str
    law_number:      str
    law_year:        Optional[str] = None
    source_type:     SourceType    = "FEDERAL_LAW"
    articles:        List[str]
    article_range:   Optional[str] = None
    raw_keys:        List[str]
    formatted:       str
    formatted_en:    str
    evolution_chain: List[str]     = Field(default_factory=list)


class Warning(BaseModel):
    """Frozen Warning contract v1.0."""
    schema_version: str          = WARNING_SCHEMA_VERSION
    severity:       WarningSeverity
    code:           str
    message_ar:     str
    message_en:     str
    is_blocking:    bool         = False


class ResponseMetadata(BaseModel):
    """Frozen ResponseMetadata contract v1.0."""
    schema_version:          str   = METADATA_SCHEMA_VERSION
    confidence:              float
    verification_score:      float
    claim_grounding_score:   float
    retrieval_latency_ms:    float = 0.0
    rerank_latency_ms:       float = 0.0
    generation_latency_ms:   float = 0.0
    verification_latency_ms: float = 0.0
    composition_latency_ms:  float = 0.0
    total_latency_ms:        float = 0.0
    retrieved_chunks:   int   = 0
    reranked_chunks:    int   = 0
    graph_nodes:        int   = 0
    graph_edges:        int   = 0
    total_claims:       int   = 0
    claims_verified:    int   = 0
    compressed_tokens:  int   = 0
    retrieval_strategy:  str  = "adaptive_multi_strategy"
    generation_strategy: str  = "COMPLETE"
    verification_status: str  = "PASS"
    composer_profile:    str  = "LEGAL_STANDARD"
    laws_used:    List[str]   = Field(default_factory=list)
    articles_used: List[str]  = Field(default_factory=list)
    generator_version: str    = "1.1"
    verifier_version:  str    = "1.0"
    composer_version:  str    = COMPOSER_CONTRACT_VERSION
    repair_mode:       str    = "NONE"
    cache_hit:         bool   = False
    pipeline_trace_id: str    = ""


class ResponseOutput(BaseModel):
    """
    Frozen public contract returned by ResponseComposer v1.0.
    The ONLY object consumed by API, UI, tests, and all downstream clients.
    """
    contract_version:     str               = COMPOSER_CONTRACT_VERSION
    response_id:          str
    answer:               str
    citations:            List[Citation]
    warnings:             List[Warning]
    confidence:           float
    metadata:             ResponseMetadata
    pipeline_trace_id:    str
    answer_language:      str               = "ar"
    citation_language:    str               = "ar"
    status:               ResponseStatus
    presentation_profile: PresentationProfile = "LEGAL_STANDARD"
    parts:                List[str]         = Field(default_factory=list)
    is_streaming_ready:   bool              = True


# ============================================================================
# ENGINE 0 -- Contract Validator  (Deterministic -- 0ms)
# ============================================================================

class ContractValidator:
    """
    Pre-composition safety gate.
    Validates GeneratorOutput x VerificationResult x EvidenceReasoningGraph.

    Checks: schema versions, null-safety, claim count drift, duplicate claim IDs,
    ready_for_response consistency.
    """

    @classmethod
    def validate(
        cls,
        gen_out:        GeneratorOutput,
        ver_result:     VerificationResult,
        evidence_graph: EvidenceReasoningGraph,
    ) -> tuple:
        errors = []

        if getattr(gen_out, "generator_schema_version", None) != "1.1":
            errors.append(
                "CONTRACT_ERROR: GeneratorOutput schema mismatch -- "
                f"expected=1.1, got={getattr(gen_out, 'generator_schema_version', None)}"
            )
        if getattr(ver_result, "verification_schema_version", None) != "1.0":
            errors.append(
                "CONTRACT_ERROR: VerificationResult schema mismatch -- "
                f"expected=1.0, got={getattr(ver_result, 'verification_schema_version', None)}"
            )

        if not getattr(gen_out, "structured_answer", None):
            errors.append("CONTRACT_ERROR: GeneratorOutput.structured_answer is None or empty")
        if evidence_graph is None:
            errors.append("CONTRACT_ERROR: EvidenceReasoningGraph is None")

        gen_count = len(gen_out.claims) if gen_out else 0
        ver_count = (
            len(ver_result.verified_claims or [])
            + len(ver_result.unsupported_claims or [])
            + len(ver_result.repaired_claims or [])
        ) if ver_result else 0
        if abs(gen_count - ver_count) > MAX_CLAIM_DRIFT:
            errors.append(
                f"CONTRACT_ERROR: Claim count drift -- "
                f"Generator={gen_count}, Verifier totals={ver_count} (tolerance={MAX_CLAIM_DRIFT})"
            )

        if gen_out:
            ids = [c.claim_id for c in gen_out.claims]
            if len(ids) != len(set(ids)):
                errors.append("CONTRACT_ERROR: Duplicate claim_ids in GeneratorOutput.claims")

        if ver_result and ver_result.overall_status == "FAIL" and ver_result.ready_for_response:
            errors.append("CONTRACT_WARNING: VerificationResult FAIL but ready_for_response=True")

        is_valid = not any("CONTRACT_ERROR:" in e for e in errors)
        return is_valid, errors


# ============================================================================
# ENGINE 1 -- Response Selector  (Deterministic -- 0ms)
# ============================================================================

class ResponseSelector:
    """
    Routes to the correct answer path based on VerificationStatus.

    BLOCKING = contradiction present AND zero verified claims.
    Structured_answer is NEVER exposed on REFUSAL or BLOCKING paths.
    """

    @classmethod
    def select(cls, ver_result: VerificationResult, gen_out: GeneratorOutput) -> tuple:
        if cls._is_blocking(ver_result):
            logger.warning("[Composer/E1] BLOCKING detected -- issuing refusal.")
            return "REFUSAL", ""

        status = ver_result.overall_status
        if status == "PASS":
            return "ANSWER", gen_out.structured_answer
        if status == "PASS_WITH_WARNINGS":
            return "PARTIAL_ANSWER", gen_out.structured_answer
        if status == "REPAIRED":
            return "ANSWER", (ver_result.repaired_answer or gen_out.structured_answer)
        # FAIL
        if not ver_result.ready_for_response:
            return "REFUSAL", ""
        return "PARTIAL_ANSWER", gen_out.structured_answer

    @classmethod
    def _is_blocking(cls, ver_result: VerificationResult) -> bool:
        all_claims = (
            list(ver_result.verified_claims or [])
            + list(ver_result.unsupported_claims or [])
            + list(ver_result.repaired_claims or [])
        )
        has_contradiction = any(getattr(c, "status", "") == "CONTRADICTED" for c in all_claims)
        no_verified       = len(ver_result.verified_claims or []) == 0
        return has_contradiction and no_verified


# ============================================================================
# ENGINE 2 -- Answer Builder  (Deterministic -- 0ms)
# ============================================================================

class AnswerBuilder:
    """
    Builds the final answer body. Never invents content.
    Control char stripping, markdown normalisation, large-response splitting.
    """

    _CONTROL_CHARS   = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
    _BROKEN_HEADINGS = re.compile(r'#{4,}')
    _BROKEN_BOLD     = re.compile(r'\*{4,}')
    _MULTI_BLANK     = re.compile(r'\n{3,}')

    @classmethod
    def build(
        cls,
        answer_text: str,
        ver_result:  VerificationResult,
        gen_out:     GeneratorOutput,
        status:      ResponseStatus,
    ) -> tuple:
        if status == "REFUSAL" or not answer_text.strip():
            answer_text = cls._build_refusal(ver_result, gen_out)
        answer_text = cls._CONTROL_CHARS.sub('', answer_text)
        answer_text = cls._BROKEN_HEADINGS.sub('###', answer_text)
        answer_text = cls._BROKEN_BOLD.sub('**', answer_text)
        answer_text = cls._MULTI_BLANK.sub('\n\n', answer_text).strip()

        parts = []
        if len(answer_text) > MAX_ANSWER_CHARS:
            parts = cls._split_answer(answer_text)
            answer_text = parts[0] if parts else answer_text

        return answer_text, parts

    @classmethod
    def _build_refusal(cls, ver_result: VerificationResult, gen_out: GeneratorOutput) -> str:
        gaps = getattr(gen_out, "unresolved_gaps", []) or []
        gap_texts = [g.description for g in gaps[:2] if getattr(g, "description", None)]
        if gap_texts:
            return f"\u0644\u0627 \u064a\u0645\u0643\u0646 \u062a\u0642\u062f\u064a\u0645 \u0625\u062c\u0627\u0628\u0629 \u0643\u0627\u0645\u0644\u0629 \u0639\u0644\u0649 \u0647\u0630\u0627 \u0627\u0644\u0633\u0624\u0627\u0644. \u0627\u0644\u0633\u0628\u0628: {'\u060c '.join(gap_texts)}."
        failure = getattr(ver_result, "failure_mode", "NONE") if ver_result else "NONE"
        if failure in ("UNSUPPORTED_CLAIM", "INVALID_CITATION", "ORPHAN_CITATION"):
            return "\u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u0623\u062f\u0644\u0629 \u0643\u0627\u0641\u064a\u0629 \u0641\u064a \u0627\u0644\u0645\u0633\u062a\u0646\u062f\u0627\u062a \u0627\u0644\u0645\u062a\u0627\u062d\u0629 \u0644\u0644\u0625\u062c\u0627\u0628\u0629 \u0639\u0644\u0649 \u0647\u0630\u0627 \u0627\u0644\u0633\u0624\u0627\u0644."
        return "\u0644\u0627 \u064a\u0645\u0643\u0646 \u0627\u0644\u0625\u062c\u0627\u0628\u0629 \u0639\u0644\u0649 \u0647\u0630\u0627 \u0627\u0644\u0633\u0624\u0627\u0644 \u0628\u0634\u0643\u0644 \u0645\u0648\u062b\u0648\u0642 \u0627\u0633\u062a\u0646\u0627\u062f\u0627\u064b \u0625\u0644\u0649 \u0627\u0644\u0645\u0633\u062a\u0646\u062f\u0627\u062a \u0627\u0644\u0645\u062a\u0627\u062d\u0629."

    @classmethod
    def _split_answer(cls, text: str) -> list:
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        parts, current, current_len = [], [], 0
        for para in paragraphs:
            if current_len + len(para) > MAX_ANSWER_CHARS and current:
                parts.append('\n\n'.join(current))
                current, current_len = [para], len(para)
            else:
                current.append(para)
                current_len += len(para)
        if current:
            parts.append('\n\n'.join(current))
        return parts


# ============================================================================
# ENGINE 3 -- Citation Composer  (Deterministic -- 0ms)
# ============================================================================

class CitationComposer:
    """
    Deduplicates, groups by law, sorts, collapses article ranges,
    and formats citation keys into Arabic Citation objects.

    Law year priority: EvidenceReasoningGraph -> document metadata -> None
    Article collapsing: [16, 17, 18] -> "16-18"
    """

    _LABELS_AR = {
        "FEDERAL_LAW":           "\u0627\u0644\u0642\u0627\u0646\u0648\u0646 \u0627\u0644\u0627\u062a\u062d\u0627\u062f\u064a",
        "CABINET_RESOLUTION":    "\u0642\u0631\u0627\u0631 \u0645\u062c\u0644\u0633 \u0627\u0644\u0648\u0632\u0631\u0627\u0621",
        "EXECUTIVE_REGULATION":  "\u0627\u0644\u0644\u0627\u0626\u062d\u0629 \u0627\u0644\u062a\u0646\u0641\u064a\u0630\u064a\u0629",
        "MINISTERIAL_RESOLUTION":"\u0627\u0644\u0642\u0631\u0627\u0631 \u0627\u0644\u0648\u0632\u0627\u0631\u064a",
        "CIRCULAR":              "\u0627\u0644\u062a\u0639\u0645\u064a\u0645",
        "GUIDELINE":             "\u0627\u0644\u0625\u0631\u0634\u0627\u062f\u0627\u062a",
        "UNKNOWN":               "\u0645\u0633\u062a\u0646\u062f \u0642\u0627\u0646\u0648\u0646\u064a",
    }
    _LABELS_EN = {
        "FEDERAL_LAW":           "Federal Law",
        "CABINET_RESOLUTION":    "Cabinet Resolution",
        "EXECUTIVE_REGULATION":  "Executive Regulation",
        "MINISTERIAL_RESOLUTION":"Ministerial Resolution",
        "CIRCULAR":              "Circular",
        "GUIDELINE":             "Guideline",
        "UNKNOWN":               "Legal Document",
    }
    _PREFIX_MAP = [
        ("CAB",  "CABINET_RESOLUTION"),
        ("MIN",  "MINISTERIAL_RESOLUTION"),
        ("EXEC", "EXECUTIVE_REGULATION"),
        ("CIRC", "CIRCULAR"),
        ("GUID", "GUIDELINE"),
    ]

    @classmethod
    def compose(cls, gen_out: GeneratorOutput, evidence_graph: EvidenceReasoningGraph) -> list:
        raw_keys = list(dict.fromkeys(gen_out.citations_bound or []))
        if not raw_keys:
            return []

        law_groups: dict = {}
        for key in raw_keys:
            parsed = cls._parse_key(key, evidence_graph)
            if parsed is None:
                continue
            gk = parsed["law_number"]
            if gk not in law_groups:
                law_groups[gk] = {
                    "law_number":  parsed["law_number"],
                    "law_year":    parsed["law_year"],
                    "source_type": parsed["source_type"],
                    "articles":    [],
                    "raw_keys":    [],
                }
            art = parsed["article_key"]
            if art not in law_groups[gk]["articles"]:
                law_groups[gk]["articles"].append(art)
            law_groups[gk]["raw_keys"].append(key)
            if not law_groups[gk]["law_year"] and parsed["law_year"]:
                law_groups[gk]["law_year"] = parsed["law_year"]

        citations = []
        for _, group in sorted(law_groups.items(), key=lambda x: cls._sort_key(x[0])):
            law_num   = group["law_number"]
            law_year  = group["law_year"]
            src_type  = group["source_type"]
            articles  = sorted(group["articles"], key=lambda a: int(a) if a.isdigit() else 9999)
            art_range = cls._collapse_range(articles)

            label_ar = cls._LABELS_AR.get(src_type, "\u0627\u0644\u0642\u0627\u0646\u0648\u0646")
            label_en = cls._LABELS_EN.get(src_type, "Law")

            if law_year:
                title_ar = f"{label_ar} \u0631\u0642\u0645 {law_num} \u0644\u0633\u0646\u0629 {law_year}"
                title_en = f"{label_en} No. {law_num} of {law_year}"
            else:
                title_ar = f"{label_ar} \u0631\u0642\u0645 {law_num}"
                title_en = f"{label_en} No. {law_num}"

            if len(articles) == 1:
                fmt_ar = f"\u0627\u0644\u0645\u0627\u062f\u0629 {articles[0]} \u0645\u0646 {title_ar}"
                fmt_en = f"Article {articles[0]} of {title_en}"
            elif art_range:
                fmt_ar = f"\u0627\u0644\u0645\u0648\u0627\u062f {art_range} \u0645\u0646 {title_ar}"
                fmt_en = f"Articles {art_range} of {title_en}"
            else:
                joined = "\u060c ".join(articles)
                fmt_ar = f"\u0627\u0644\u0645\u0648\u0627\u062f {joined} \u0645\u0646 {title_ar}"
                fmt_en = f"Articles {', '.join(articles)} of {title_en}"

            citations.append(Citation(
                citation_key=f"LAW{law_num}_GROUPED",
                law_title=title_ar,
                law_number=law_num,
                law_year=law_year,
                source_type=src_type,
                articles=articles,
                article_range=art_range,
                raw_keys=group["raw_keys"],
                formatted=fmt_ar,
                formatted_en=fmt_en,
                evolution_chain=cls._build_evolution_chain(law_num, evidence_graph),
            ))
        return citations

    @classmethod
    def _parse_key(cls, key: str, graph: EvidenceReasoningGraph) -> Optional[dict]:
        m = re.match(r"LAW(\d+)_ART(\d+)", key, re.IGNORECASE)
        if not m:
            return None
        law_num = m.group(1)
        art_key = m.group(2)
        src_type = "FEDERAL_LAW"
        for prefix, st in cls._PREFIX_MAP:
            if key.upper().startswith(prefix):
                src_type = st
                break
        law_year = None
        if graph and graph.nodes:
            for node in graph.nodes.values():
                if node.law_number == law_num and getattr(node, "law_year", None):
                    law_year = node.law_year
                    break
        return {"law_number": law_num, "article_key": art_key, "law_year": law_year, "source_type": src_type}

    @classmethod
    def _collapse_range(cls, articles: list) -> Optional[str]:
        nums = []
        for a in articles:
            try:
                nums.append(int(a))
            except ValueError:
                return None
        if not nums or len(nums) < 2:
            return None
        nums.sort()
        if nums == list(range(nums[0], nums[-1] + 1)):
            return f"{nums[0]}\u2013{nums[-1]}"
        return None

    @classmethod
    def _sort_key(cls, law_num: str) -> int:
        try:
            return int(law_num)
        except ValueError:
            return 9999

    @classmethod
    def _build_evolution_chain(cls, law_num: str, graph: EvidenceReasoningGraph) -> list:
        chain = []
        if not graph:
            return chain
        for edge in (graph.relational_edges or []):
            src = edge.get("source", "")
            rel = edge.get("relation", "")
            if f"LAW_{law_num}_" in src and rel in ("supersedes", "amended_by"):
                target = edge.get("target_article", edge.get("target", ""))
                chain.append(f"\u062a\u0645 \u062a\u0639\u062f\u064a\u0644\u0647: {target}")
        return chain[:3]


# ============================================================================
# ENGINE 4 -- Warning Composer  (Deterministic -- 0ms)
# ============================================================================

class WarningComposer:
    """
    Translates gap taxonomy, unsupported claims, consistency errors
    into severity-ordered, deduplicated Arabic warnings.

    Severity: BLOCKING > CRITICAL > WARNING > INFO
    Sort key: (severity_rank ASC, code ASC) -- byte-identical snapshots.
    """

    _SEVERITY_ORDER = {"BLOCKING": 0, "CRITICAL": 1, "WARNING": 2, "INFO": 3}

    _CLAIM_STATUS_MAP = {
        "UNSUPPORTED":      ("WARNING",  "UNSUPPORTED_CLAIM",
                             "\u062a\u0646\u0628\u064a\u0647: \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u062f\u0644\u064a\u0644 \u0643\u0627\u0641\u064d \u0644\u0628\u0639\u0636 \u0627\u0644\u0646\u0642\u0627\u0637.",
                             "Warning: Insufficient evidence found for some claims."),
        "CONTRADICTED":     ("CRITICAL", "CONTRADICTED_CLAIM",
                             "\u062a\u062d\u0630\u064a\u0631: \u062a\u0648\u062c\u062f \u0646\u0635\u0648\u0635 \u0642\u0627\u0646\u0648\u0646\u064a\u0629 \u0645\u062a\u0639\u0627\u0631\u0636\u0629. \u064a\u064f\u0631\u062c\u0649 \u0645\u0631\u0627\u062c\u0639\u0629 \u0627\u0644\u0645\u062e\u062a\u0635 \u0627\u0644\u0642\u0627\u0646\u0648\u0646\u064a.",
                             "Warning: Contradicting legal texts exist. Legal review is advised."),
        "SUPERSEDED":       ("WARNING",  "SUPERSEDED_LAW",
                             "\u062a\u0646\u0628\u064a\u0647: \u062a\u0645 \u062a\u0639\u062f\u064a\u0644 \u0628\u0639\u0636 \u0627\u0644\u0623\u062d\u0643\u0627\u0645 \u0628\u0645\u0648\u062c\u0628 \u062a\u0634\u0631\u064a\u0639 \u0644\u0627\u062d\u0642.",
                             "Warning: Some cited provisions have been amended by later legislation."),
        "WEAKLY_SUPPORTED": ("INFO",     "WEAK_EVIDENCE",
                             "\u0645\u0644\u0627\u062d\u0638\u0629: \u0628\u0639\u0636 \u0627\u0644\u0646\u0642\u0627\u0637 \u062a\u0633\u062a\u0646\u062f \u0625\u0644\u0649 \u0623\u062f\u0644\u0629 \u062c\u0632\u0626\u064a\u0629.",
                             "Note: Some points rely on partial evidence."),
        "PARTIALLY_SUPPORTED": ("INFO",  "PARTIAL_EVIDENCE",
                             "\u0645\u0644\u0627\u062d\u0638\u0629: \u0627\u0644\u0623\u062f\u0644\u0629 \u062a\u062f\u0639\u0645 \u062c\u0632\u0621\u064b\u0627 \u0645\u0646 \u0647\u0630\u0647 \u0627\u0644\u0646\u0642\u0637\u0629 \u0641\u0642\u0637.",
                             "Note: Evidence supports only part of this claim."),
        "OUT_OF_SCOPE":     ("INFO",     "OUT_OF_SCOPE_CLAIM",
                             "\u0645\u0644\u0627\u062d\u0638\u0629: \u0642\u062f \u064a\u0643\u0648\u0646 \u062c\u0632\u0621 \u0645\u0646 \u0627\u0644\u0633\u0624\u0627\u0644 \u062e\u0627\u0631\u062c \u0646\u0637\u0627\u0642 \u0627\u0644\u0645\u0633\u062a\u0646\u062f\u0627\u062a \u0627\u0644\u0645\u062a\u0627\u062d\u0629.",
                             "Note: Part of the question may be outside the scope of available documents."),
    }

    _GAP_MAP = {
        "LOW_CONFIDENCE":       ("INFO",     "LOW_CONFIDENCE",
                                 "\u0645\u0644\u0627\u062d\u0638\u0629: \u0628\u0639\u0636 \u0627\u0644\u0623\u062c\u0632\u0627\u0621 \u062a\u0633\u062a\u0646\u062f \u0625\u0644\u0649 \u0623\u062f\u0644\u0629 \u063a\u064a\u0631 \u0645\u0643\u062a\u0645\u0644\u0629.",
                                 "Note: Some parts are based on incomplete evidence."),
        "TOKEN_LIMIT":          ("INFO",     "TOKEN_COMPRESSION",
                                 "\u0645\u0644\u0627\u062d\u0638\u0629: \u062a\u0645 \u0627\u062e\u062a\u0635\u0627\u0631 \u0628\u0639\u0636 \u0627\u0644\u0646\u0635\u0648\u0635 \u0645\u0639 \u0627\u0644\u062d\u0641\u0627\u0638 \u0639\u0644\u0649 \u0627\u0644\u0645\u0636\u0645\u0648\u0646.",
                                 "Note: Some texts were compressed while preserving meaning."),
        "MISSING_ROLE":         ("INFO",     "MISSING_EXECUTIVE_REG",
                                 "\u0645\u0644\u0627\u062d\u0638\u0629: \u0644\u0645 \u062a\u062a\u0648\u0641\u0631 \u0627\u0644\u0644\u0627\u0626\u062d\u0629 \u0627\u0644\u062a\u0646\u0641\u064a\u0630\u064a\u0629 \u0644\u0647\u0630\u0627 \u0627\u0644\u062d\u0643\u0645.",
                                 "Note: The executive regulation for this provision was not found."),
        "CONFLICTING_EVIDENCE": ("CRITICAL", "CONFLICTING_EVIDENCE",
                                 "\u062a\u062d\u0630\u064a\u0631: \u062a\u0645 \u0631\u0635\u062f \u062a\u0639\u0627\u0631\u0636 \u0641\u064a \u0627\u0644\u0646\u0635\u0648\u0635 \u0627\u0644\u0642\u0627\u0646\u0648\u0646\u064a\u0629 \u0627\u0644\u0645\u0635\u062f\u0631\u064a\u0629.",
                                 "Warning: Conflicting legal texts detected in sources."),
        "INSUFFICIENT_EVIDENCE":("WARNING",  "INSUFFICIENT_EVIDENCE",
                                 "\u062a\u0646\u0628\u064a\u0647: \u0627\u0644\u0623\u062f\u0644\u0629 \u063a\u064a\u0631 \u0643\u0627\u0641\u064a\u0629 \u0644\u0644\u0625\u062c\u0627\u0628\u0629 \u0627\u0644\u0643\u0627\u0645\u0644\u0629.",
                                 "Warning: Retrieved evidence is insufficient for a complete answer."),
        "NO_RETRIEVAL":         ("WARNING",  "NO_RETRIEVAL",
                                 "\u062a\u0646\u0628\u064a\u0647: \u0644\u0645 \u064a\u062a\u0645 \u0627\u0633\u062a\u0631\u062c\u0627\u0639 \u0645\u0633\u062a\u0646\u062f\u0627\u062a \u0643\u0627\u0641\u064a\u0629.",
                                 "Warning: Insufficient documents were retrieved."),
        "OUTSIDE_SCOPE":        ("INFO",     "OUTSIDE_SCOPE",
                                 "\u0645\u0644\u0627\u062d\u0638\u0629: \u0628\u0639\u0636 \u0627\u0644\u0627\u0633\u062a\u0641\u0633\u0627\u0631 \u062e\u0627\u0631\u062c \u0646\u0637\u0627\u0642 \u0627\u0644\u0645\u0633\u062a\u0646\u062f\u0627\u062a.",
                                 "Note: Part of the query may be outside the scope of uploaded documents."),
    }

    @classmethod
    def compose(cls, gen_out: GeneratorOutput, ver_result: VerificationResult, is_repaired: bool) -> list:
        seen: set = set()
        warnings: list = []

        def add(severity: str, code: str, msg_ar: str, msg_en: str, blocking: bool = False):
            if code not in seen:
                seen.add(code)
                warnings.append(Warning(severity=severity, code=code,
                                        message_ar=msg_ar, message_en=msg_en, is_blocking=blocking))

        for claim in (ver_result.unsupported_claims or []):
            entry = cls._CLAIM_STATUS_MAP.get(getattr(claim, "status", ""))
            if entry:
                add(*entry)

        if ver_result.consistency_errors:
            add("WARNING", "CONSISTENCY_ERROR",
                "\u0645\u0644\u0627\u062d\u0638\u0629: \u062a\u0645 \u0631\u0635\u062f \u062a\u0639\u0627\u0631\u0636 \u0645\u062d\u062a\u0645\u0644 \u0628\u064a\u0646 \u0645\u0635\u0627\u062f\u0631 \u0627\u0644\u0646\u0635.",
                "Note: Potential inconsistency detected between legal text sources.")

        for gap in (getattr(gen_out, "unresolved_gaps", []) or []):
            entry = cls._GAP_MAP.get(getattr(gap, "gap_type", ""))
            if entry:
                add(*entry)

        for disc in (getattr(gen_out, "warnings_and_disclaimers", []) or []):
            code = f"GENERATOR_{abs(hash(str(disc))) % 10000:04d}"
            add("INFO", code, str(disc), str(disc))

        if is_repaired:
            add("INFO", "AUTO_REPAIRED",
                "\u0645\u0644\u0627\u062d\u0638\u0629: \u062a\u0645 \u062a\u0635\u062d\u064a\u062d \u0628\u0639\u0636 \u0627\u0644\u0646\u0642\u0627\u0637 \u062a\u0644\u0642\u0627\u0626\u064a\u064b\u0627.",
                "Note: Some points were automatically corrected.")

        warnings.sort(key=lambda w: (cls._SEVERITY_ORDER.get(w.severity, 4), w.code))
        return warnings


# ============================================================================
# ENGINE 5 -- Metadata Composer  (Deterministic -- 0ms)
# ============================================================================

class MetadataComposer:
    """
    Assembles ResponseMetadata from all upstream contracts and retrieval_trace.
    Latencies in retrieval_trace["latencies"] are in seconds; converted to ms here.
    composition_latency_ms is filled post-facto by the orchestrator.
    """

    @classmethod
    def compose(
        cls,
        gen_out:         GeneratorOutput,
        ver_result:      VerificationResult,
        evidence_graph:  EvidenceReasoningGraph,
        retrieval_trace: dict,
        profile:         str,
        trace_id:        str,
        cache_hit:       bool,
    ) -> ResponseMetadata:
        latencies = (retrieval_trace or {}).get("latencies", {})
        stats     = (retrieval_trace or {}).get("retrieval_statistics", {})

        gen_time = gen_out.metadata.generation_time_ms   if (gen_out and gen_out.metadata) else 0.0
        ver_time = ver_result.provenance.execution_time_ms if (ver_result and ver_result.provenance) else 0.0
        ret_time = float(latencies.get("retrieve", 0.0)) * 1000
        rrk_time = float(latencies.get("rerank",   0.0)) * 1000

        ver_score   = ver_result.scores.overall_score        if ver_result else 1.0
        claim_score = ver_result.scores.claim_grounding_score if ver_result else 1.0
        repair_mode = ver_result.provenance.repair_mode       if (ver_result and ver_result.provenance) else "NONE"

        graph_nodes  = len(evidence_graph.nodes)            if evidence_graph else 0
        graph_edges  = len(evidence_graph.relational_edges) if evidence_graph else 0
        comp_ratio   = gen_out.metadata.compression_ratio   if (gen_out and gen_out.metadata) else 1.0
        p_tokens     = gen_out.metadata.prompt_tokens       if (gen_out and gen_out.metadata) else 0
        compressed   = int(p_tokens * max(0.0, 1.0 - min(comp_ratio, 1.0)))

        return ResponseMetadata(
            confidence=round(ver_score, 4),
            verification_score=round(ver_score, 4),
            claim_grounding_score=round(claim_score, 4),
            retrieval_latency_ms=round(ret_time, 2),
            rerank_latency_ms=round(rrk_time, 2),
            generation_latency_ms=round(gen_time, 2),
            verification_latency_ms=round(ver_time, 2),
            composition_latency_ms=0.0,
            total_latency_ms=round(ret_time + rrk_time + gen_time + ver_time, 2),
            retrieved_chunks=int(stats.get("raw_candidates", 0)) if isinstance(stats, dict) else 0,
            reranked_chunks=int(stats.get("after_rerank",   0)) if isinstance(stats, dict) else 0,
            graph_nodes=graph_nodes,
            graph_edges=graph_edges,
            total_claims=len(gen_out.claims)            if gen_out else 0,
            claims_verified=len(ver_result.verified_claims) if ver_result else 0,
            compressed_tokens=compressed,
            retrieval_strategy=stats.get("strategy", "adaptive_multi_strategy") if isinstance(stats, dict) else "adaptive_multi_strategy",
            generation_strategy=getattr(gen_out, "generation_strategy_used", "COMPLETE"),
            verification_status=ver_result.overall_status if ver_result else "UNKNOWN",
            composer_profile=profile,
            laws_used=list(evidence_graph.laws_covered)      if evidence_graph else [],
            articles_used=list(evidence_graph.articles_covered) if evidence_graph else [],
            repair_mode=repair_mode,
            cache_hit=cache_hit,
            pipeline_trace_id=trace_id,
        )


# ============================================================================
# ENGINE 6 -- Output Formatter  (Deterministic -- 0ms)
# ============================================================================

class OutputFormatter:
    """
    Renders ResponseOutput for each delivery channel.
    Streaming (Q1): text chunks first -> single terminal metadata event.
    """

    _CHUNK_SIZE = 100

    @classmethod
    def format(cls, output: ResponseOutput, channel: OutputChannel) -> dict:
        if channel == "API":
            return output.model_dump()
        if channel == "MARKDOWN":
            return {"text": cls._to_markdown(output)}
        if channel in ("PLAIN_TEXT", "VOICE", "WHATSAPP"):
            return {"text": cls._to_plain(output)}
        if channel == "STREAMING":
            return cls._to_streaming(output)
        if channel in ("TEAMS", "SLACK"):
            return cls._to_card(output, channel)
        return output.model_dump()

    @classmethod
    def _to_markdown(cls, output: ResponseOutput) -> str:
        lines = [output.answer, ""]
        if output.citations:
            lines += ["---", "", "**\u0627\u0644\u0645\u0635\u0627\u062f\u0631 \u0627\u0644\u0642\u0627\u0646\u0648\u0646\u064a\u0629:**", ""]
            for c in output.citations:
                lines.append(f"- {c.formatted}")
            lines.append("")
        visible = [w for w in output.warnings if w.severity in ("WARNING", "CRITICAL", "BLOCKING")]
        if visible:
            lines += ["---", ""]
            for w in visible:
                icon = "\u274c" if w.severity == "BLOCKING" else ("\u26a0\ufe0f" if w.severity == "CRITICAL" else "\u2139\ufe0f")
                lines.append(f"> {icon} {w.message_ar}")
            lines.append("")
        return "\n".join(lines)

    @classmethod
    def _to_plain(cls, output: ResponseOutput) -> str:
        lines = [output.answer, ""]
        if output.citations:
            lines.append("\u0627\u0644\u0645\u0635\u0627\u062f\u0631:")
            for c in output.citations:
                lines.append(f"- {c.formatted}")
        return "\n".join(lines)

    @classmethod
    def _to_streaming(cls, output: ResponseOutput) -> dict:
        chunks = []
        text = output.answer
        for i in range(0, max(len(text), 1), cls._CHUNK_SIZE):
            chunks.append({"type": "text_delta", "text": text[i:i + cls._CHUNK_SIZE]})
        chunks.append({
            "type":        "response_complete",
            "response_id": output.response_id,
            "status":      output.status,
            "citations":   [c.model_dump() for c in output.citations],
            "warnings":    [w.model_dump() for w in output.warnings if w.severity != "INFO"],
            "confidence":  output.confidence,
            "metadata":    output.metadata.model_dump(),
        })
        return {"chunks": chunks}

    @classmethod
    def _to_card(cls, output: ResponseOutput, channel: str) -> dict:
        return {
            "text":       output.answer,
            "citations":  [c.formatted for c in output.citations],
            "warnings":   [w.message_ar for w in output.warnings
                           if w.severity in ("WARNING", "CRITICAL", "BLOCKING")],
            "confidence": f"{output.confidence:.0%}",
            "channel":    channel,
        }


# ============================================================================
# HELPERS
# ============================================================================

def _build_response_id(
    pipeline_trace_id: str,
    gen_out:           Optional[GeneratorOutput],
    ver_result:        Optional[VerificationResult],
) -> str:
    """
    Deterministic, repeatable response ID.
    SHA256(pipeline_trace_id + generator_answer_sha256[:8] + verifier_status_sha256[:8])
    """
    gen_hash = hashlib.sha256(
        (gen_out.structured_answer if gen_out else "").encode("utf-8", errors="replace")
    ).hexdigest()[:8]
    ver_hash = hashlib.sha256(
        (ver_result.overall_status if ver_result else "UNKNOWN").encode()
    ).hexdigest()[:8]
    combined = f"{pipeline_trace_id}:{gen_hash}:{ver_hash}"
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


def _detect_language(text: str) -> str:
    if not text:
        return "ar"
    arabic_chars = sum(1 for c in text if '\u0600' <= c <= '\u06FF')
    return "ar" if arabic_chars > len(text) * 0.25 else "en"


# ============================================================================
# ORCHESTRATOR
# ============================================================================

class ResponseComposer:
    """
    Phase 8 -- Response Composer v1.0

    Orchestrates all 7 sub-engines: GenerationArtifacts -> ResponseOutput v1.0.
    This is the ONLY object consumed by API, UI, and downstream clients.
    GeneratorOutput, VerificationResult, GenerationArtifacts are internal-only.
    """

    VERSION = COMPOSER_CONTRACT_VERSION

    def compose(
        self,
        generation_artifacts: Optional[dict],
        retrieval_trace:      dict,
        pipeline_trace_id:    str,
        profile:              str = "LEGAL_STANDARD",
        channel:              str = "API",
        cache_hit:            bool = False,
    ) -> ResponseOutput:
        t_start = time.time()

        artifacts      = generation_artifacts or {}
        gen_out        = artifacts.get("generator_output")
        evidence_graph = artifacts.get("evidence_reasoning_graph")
        ver_result     = artifacts.get("verification_result")
        cached_answer  = artifacts.get("cached_answer", "")

        # -- Cache hit path (Q3): full contract, empty citations/warnings -------
        if cache_hit or gen_out is None or ver_result is None:
            comp_ms = round((time.time() - t_start) * 1000, 2)
            rid = hashlib.sha256(f"CACHE:{pipeline_trace_id}".encode()).hexdigest()[:16]
            meta = ResponseMetadata(
                confidence=1.0, verification_score=1.0, claim_grounding_score=1.0,
                composition_latency_ms=comp_ms, total_latency_ms=comp_ms,
                retrieval_strategy="CACHE", generation_strategy="CACHE",
                verification_status="CACHE_HIT", composer_profile=profile,
                cache_hit=True, pipeline_trace_id=pipeline_trace_id,
            )
            return ResponseOutput(
                response_id=rid, answer=cached_answer,
                citations=[], warnings=[], confidence=1.0, metadata=meta,
                pipeline_trace_id=pipeline_trace_id,
                answer_language=_detect_language(cached_answer),
                status="ANSWER", presentation_profile=profile,
            )

        # -- Engine 0: Contract Validator ---------------------------------------
        is_valid, validation_errors = ContractValidator.validate(gen_out, ver_result, evidence_graph)
        if not is_valid:
            logger.error(f"[Composer/E0] Contract violations: {validation_errors}")
        elif validation_errors:
            logger.warning(f"[Composer/E0] Contract warnings: {validation_errors}")

        # -- Engine 1: Response Selector ----------------------------------------
        response_status, selected_answer = ResponseSelector.select(ver_result, gen_out)

        # -- Engine 2: Answer Builder --------------------------------------------
        final_answer, answer_parts = AnswerBuilder.build(selected_answer, ver_result, gen_out, response_status)

        # -- Engine 3: Citation Composer ----------------------------------------
        citations = CitationComposer.compose(gen_out, evidence_graph)

        citation_warning = None
        if not citations and final_answer and response_status in ("ANSWER", "PARTIAL_ANSWER"):
            citation_warning = Warning(
                severity="WARNING", code="MISSING_CITATIONS",
                message_ar="\u062a\u0646\u0628\u064a\u0647: \u0644\u0645 \u064a\u062a\u0645 \u062a\u062d\u062f\u064a\u062f \u0645\u0635\u0627\u062f\u0631 \u0642\u0627\u0646\u0648\u0646\u064a\u0629 \u0645\u062d\u062f\u062f\u0629 \u0644\u0647\u0630\u0647 \u0627\u0644\u0625\u062c\u0627\u0628\u0629.",
                message_en="Warning: No specific legal sources were identified for this answer.",
            )

        if citations and not final_answer.strip():
            response_status = "REFUSAL"
            final_answer, answer_parts = AnswerBuilder.build("", ver_result, gen_out, "REFUSAL")

        # -- Engine 4: Warning Composer -----------------------------------------
        is_repaired = (ver_result.overall_status == "REPAIRED")
        warnings = WarningComposer.compose(gen_out, ver_result, is_repaired)
        if citation_warning:
            warnings.append(citation_warning)
        warnings.sort(key=lambda w: (WarningComposer._SEVERITY_ORDER.get(w.severity, 4), w.code))

        # -- Engine 5: Metadata Composer ----------------------------------------
        metadata = MetadataComposer.compose(
            gen_out, ver_result, evidence_graph,
            retrieval_trace, profile, pipeline_trace_id, cache_hit,
        )

        comp_ms = round((time.time() - t_start) * 1000, 2)
        metadata.composition_latency_ms = comp_ms
        metadata.total_latency_ms       = round(metadata.total_latency_ms + comp_ms, 2)

        response_id = _build_response_id(pipeline_trace_id, gen_out, ver_result)

        # -- Engine 6: Output Formatter (assemble ResponseOutput) ---------------
        output = ResponseOutput(
            response_id=response_id,
            answer=final_answer,
            citations=citations,
            warnings=warnings,
            confidence=round(metadata.confidence, 4),
            metadata=metadata,
            pipeline_trace_id=pipeline_trace_id,
            answer_language=_detect_language(final_answer),
            citation_language="ar",
            status=response_status,
            presentation_profile=profile,
            parts=answer_parts,
            is_streaming_ready=True,
        )

        logger.info(
            f"[Composer] v{self.VERSION} | status={response_status} | "
            f"citations={len(citations)} | warnings={len(warnings)} | "
            f"confidence={output.confidence:.3f} | comp_ms={comp_ms:.1f}"
        )
        return output
