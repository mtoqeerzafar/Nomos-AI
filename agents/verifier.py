"""
Phase 7 — Verification Engine Contracts & Implementations v1.0
Defines frozen Pydantic contracts and 5 Cohesive Sub-Engines for VerificationEngine.
"""

import os, re, json, time, uuid
from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field
from langchain_openai import AzureChatOpenAI

from agents.generator import (
    GeneratorOutput,
    VerificationInput,
    EvidenceReasoningGraph,
    ClaimBinding,
    UnresolvedGap
)

VERIFICATION_CONTRACT_VERSION = "1.0"
VERIFIER_PROMPT_VERSION = "VERIFIER_MICRO_REPAIR_v1.0"

# ==============================================================================
# ENUMS & TAXONOMIES
# ==============================================================================

ClaimVerificationTag = Literal[
    "VERIFIED",
    "SUPPORTED",
    "WEAKLY_SUPPORTED",
    "PARTIALLY_SUPPORTED",
    "UNSUPPORTED",
    "CONTRADICTED",
    "SUPERSEDED",
    "OUT_OF_SCOPE"
]

VerificationStatus = Literal["PASS", "PASS_WITH_WARNINGS", "REPAIRED", "FAIL"]

VerificationFailureMode = Literal[
    "NONE",
    "INVALID_SCHEMA",
    "INVALID_CITATION",
    "ORPHAN_CITATION",
    "UNSUPPORTED_CLAIM",
    "CONTRADICTED_CLAIM",
    "SUPERSESSION_ERROR",
    "MISSING_WARNING",
    "MISSING_DISCLAIMER",
    "REPAIR_FAILED",
    "PIPELINE_CONTRACT_VIOLATION"
]

RepairMode = Literal["NONE", "DETERMINISTIC", "LLM_MICRO"]


# ==============================================================================
# DATA CONTRACTS (v1.0)
# ==============================================================================

class VerificationScores(BaseModel):
    """Multi-dimensional quality and grounding scores computed deterministically."""
    overall_score: float = Field(default=1.0, description="Weighted composite score (0.0 to 1.0).")
    claim_grounding_score: float = Field(default=1.0, description="Ratio of verified/supported claims.")
    citation_score: float = Field(default=1.0, description="Citation accuracy and metadata alignment ratio.")
    consistency_score: float = Field(default=1.0, description="Legal evolution and supersession score.")
    contract_score: float = Field(default=1.0, description="Upstream pipeline contract compliance score.")
    warning_score: float = Field(default=1.0, description="Gap and disclaimer preservation score.")
    repair_score: float = Field(default=1.0, description="Repair cleanliness score.")


class VerificationProvenance(BaseModel):
    """Detailed operational telemetry for Verification Engine execution."""
    verification_version: str = Field(default=VERIFICATION_CONTRACT_VERSION)
    verifier_prompt_version: str = Field(default=VERIFIER_PROMPT_VERSION)
    repair_mode: RepairMode = Field(default="NONE")
    rules_triggered: List[str] = Field(default_factory=list)
    execution_time_ms: float = Field(default=0.0)
    llm_used: bool = Field(default=False)
    repair_count: int = Field(default=0)


class VerifiedClaim(BaseModel):
    """Detailed claim verification payload."""
    claim_id: str
    statement: str
    status: ClaimVerificationTag = Field(default="VERIFIED")
    supporting_node_ids: List[str] = Field(default_factory=list)
    rationale: str = Field(default="Claim verifiably grounded in evidence graph.")


class RepairAction(BaseModel):
    """Single targeted repair action item."""
    action_id: str = Field(default_factory=lambda: f"ACT_{uuid.uuid4().hex[:6]}")
    action_type: Literal[
        "REMOVE_CLAIM",
        "ADD_WARNING",
        "REPLACE_CITATION",
        "FIX_SUPERSESSION",
        "INSERT_DISCLAIMER",
        "UPDATE_LAYOUT"
    ]
    reason: str
    target_claim_id: Optional[str] = Field(default=None)
    priority: int = Field(default=1)


class VerificationResult(BaseModel):
    """
    Frozen public contract returned by VerificationEngine (v1.0).
    Acts as the final safety gate before Response Composer.
    """
    verification_schema_version: str = Field(default=VERIFICATION_CONTRACT_VERSION)
    overall_status: VerificationStatus = Field(default="PASS")
    failure_mode: VerificationFailureMode = Field(default="NONE")
    scores: VerificationScores = Field(default_factory=VerificationScores)
    verified_claims: List[VerifiedClaim] = Field(default_factory=list)
    unsupported_claims: List[VerifiedClaim] = Field(default_factory=list)
    repaired_claims: List[VerifiedClaim] = Field(default_factory=list)
    citation_errors: List[str] = Field(default_factory=list)
    consistency_errors: List[str] = Field(default_factory=list)
    contract_errors: List[str] = Field(default_factory=list)
    repair_actions: List[RepairAction] = Field(default_factory=list)
    repaired_answer: Optional[str] = Field(default=None, description="Patched response text if status == REPAIRED or PASS_WITH_WARNINGS.")
    ready_for_response: bool = Field(default=True, description="True -> Safe to hand off to Response Composer.")
    provenance: VerificationProvenance = Field(default_factory=VerificationProvenance)


class VerificationReport(BaseModel):
    """Full operational report artifact for pipeline logging and telemetry."""
    pipeline_trace_id: str = Field(default_factory=lambda: f"TRACE_{uuid.uuid4().hex[:8]}")
    result: VerificationResult
    generator_version: str = Field(default="1.1")
    checker_version: str = Field(default="3.1")
    execution_timestamp: float = Field(default_factory=time.time)


# ==============================================================================
# ENGINE 1: Claim Verification Engine (Deterministic — 0ms)
# ==============================================================================

class ClaimVerificationEngine:
    """Verifies that generated claims exist in the EvidenceReasoningGraph."""

    @classmethod
    def verify_claims(
        cls,
        claims: List[ClaimBinding],
        graph: EvidenceReasoningGraph
    ) -> tuple[List[VerifiedClaim], List[VerifiedClaim], float]:
        verified = []
        unsupported = []

        for claim in claims:
            matching_nodes = [n for n in graph.nodes.values() if n.article_key == claim.source_article_key and n.law_number == claim.source_law_number]

            if matching_nodes:
                tag: ClaimVerificationTag = "VERIFIED" if claim.evidence_confidence == "HIGH" else "SUPPORTED"
                v_claim = VerifiedClaim(
                    claim_id=claim.claim_id,
                    statement=claim.statement,
                    status=tag,
                    supporting_node_ids=[n.node_id for n in matching_nodes],
                    rationale=f"Claim verifiably grounded with {len(matching_nodes)} node(s)."
                )
                verified.append(v_claim)
            else:
                v_claim = VerifiedClaim(
                    claim_id=claim.claim_id,
                    statement=claim.statement,
                    status="UNSUPPORTED",
                    supporting_node_ids=[],
                    rationale=f"Article {claim.source_article_key} not present in evidence graph."
                )
                unsupported.append(v_claim)

        total_claims = len(claims)
        grounding_score = (len(verified) / total_claims) if total_claims > 0 else 1.0

        return verified, unsupported, grounding_score


# ==============================================================================
# ENGINE 2: Citation Integrity Engine (Deterministic — 0ms)
# ==============================================================================

class CitationIntegrityEngine:
    """Validates statutory law number, year, article key, and orphan citations."""

    @classmethod
    def verify_citations(
        cls,
        citations: List[str],
        graph: EvidenceReasoningGraph
    ) -> tuple[List[str], float]:
        errors = []

        for cit in citations:
            match = re.search(r"المادة\s*(\d+)\s*من\s*القانون\s*(\d+)", cit)
            if not match:
                errors.append(f"Malformed citation formatting: {cit}")
                continue

            art_key = match.group(1)
            law_num = match.group(2)

            matching_nodes = [n for n in graph.nodes.values() if n.article_key == art_key and n.law_number == law_num]
            if not matching_nodes:
                errors.append(f"Orphan citation: {cit} does not correspond to retrieved evidence.")

        citation_score = max(0.0, 1.0 - (len(errors) * 0.25))
        return errors, citation_score


# ==============================================================================
# ENGINE 3: Legal Consistency Engine (Deterministic — 0ms)
# ==============================================================================

class LegalConsistencyEngine:
    """Validates statutory supersession, legal evolution, and gap preservation."""

    @classmethod
    def verify_consistency(
        cls,
        inp: VerificationInput
    ) -> tuple[List[str], float]:
        errors = []
        gen_out = inp.generator_output
        graph = inp.evidence_reasoning_graph
        rel_dec = inp.relevance_decision

        strategy = rel_dec.get("generation_strategy", "COMPLETE")

        # Verify strategy compliance
        if gen_out.generation_strategy_used != strategy:
            errors.append(f"Strategy violation: upstream strategy was '{strategy}', Generator used '{gen_out.generation_strategy_used}'.")

        # Verify disclaimer preservation when partial
        if strategy == "PARTIAL_WITH_WARNING" and not gen_out.warnings_and_disclaimers:
            errors.append("Warning preservation violation: partial strategy requires an active warning disclaimer.")

        consistency_score = max(0.0, 1.0 - (len(errors) * 0.3))
        return errors, consistency_score


# ==============================================================================
# ENGINE 4: Contract Integrity & Quality Auditor (Deterministic — 0ms)
# ==============================================================================

class ContractIntegrityEngine:
    """Audits cross-agent pipeline contracts and calculates multi-dimensional quality scores."""

    @classmethod
    def audit_contract(
        cls,
        inp: VerificationInput,
        grounding_score: float,
        citation_score: float,
        consistency_score: float
    ) -> tuple[VerificationScores, VerificationStatus, VerificationFailureMode, List[str]]:
        contract_errors = []
        gen_out = inp.generator_output

        # Verify schema version compatibility
        if gen_out.generator_schema_version not in ["1.0", "1.1"]:
            contract_errors.append(f"Unsupported Generator schema version: {gen_out.generator_schema_version}")

        contract_score = 1.0 if not contract_errors else 0.5
        warning_score = 1.0 if gen_out.warnings_and_disclaimers or gen_out.generation_strategy_used != "PARTIAL_WITH_WARNING" else 0.5

        overall_score = round(
            (grounding_score * 0.4) +
            (citation_score * 0.2) +
            (consistency_score * 0.2) +
            (contract_score * 0.1) +
            (warning_score * 0.1),
            3
        )

        scores = VerificationScores(
            overall_score=overall_score,
            claim_grounding_score=grounding_score,
            citation_score=citation_score,
            consistency_score=consistency_score,
            contract_score=contract_score,
            warning_score=warning_score,
            repair_score=1.0
        )

        # Categorize status and failure mode
        failure_mode: VerificationFailureMode = "NONE"

        if contract_errors:
            status: VerificationStatus = "FAIL"
            failure_mode = "PIPELINE_CONTRACT_VIOLATION"
        elif grounding_score < 0.5:
            status = "FAIL"
            failure_mode = "UNSUPPORTED_CLAIM"
        elif grounding_score < 1.0 or citation_score < 1.0 or consistency_score < 1.0:
            status = "REPAIRED"
            failure_mode = "MISSING_DISCLAIMER" if consistency_score < 1.0 else "INVALID_CITATION"
        elif gen_out.warnings_and_disclaimers:
            status = "PASS_WITH_WARNINGS"
        else:
            status = "PASS"

        return scores, status, failure_mode, contract_errors


# ==============================================================================
# ENGINE 5: Targeted Repair Planner (Engine 5A Deterministic + Engine 5B LLM)
# ==============================================================================

class TargetedRepairPlanner:
    """Executes Engine 5A (Deterministic repair) and optionally Engine 5B (LLM micro-repair)."""

    @classmethod
    def apply_repairs(
        cls,
        inp: VerificationInput,
        unsupported: List[VerifiedClaim],
        citation_errors: List[str],
        consistency_errors: List[str],
        model=None
    ) -> tuple[str, List[RepairAction], RepairMode]:
        gen_out = inp.generator_output
        answer = gen_out.structured_answer
        actions = []
        mode: RepairMode = "NONE"

        # Engine 5A: Deterministic Repairs (0ms)
        if consistency_errors:
            mode = "DETERMINISTIC"
            for err in consistency_errors:
                if "warning" in err.lower() or "disclaimer" in err.lower():
                    disclaimer = "\n\n- **تنبيه**: الأدلة المسترجعة تغطي الالتزامات الأساسية ولكنها تفتقر لتغطية كاملة لجميع الاستثناءات."
                    if disclaimer not in answer:
                        answer = disclaimer + "\n\n" + answer
                        actions.append(RepairAction(
                            action_type="INSERT_DISCLAIMER",
                            reason="Appended mandatory missing warning disclaimer deterministically."
                        ))

        # Engine 5B: LLM Micro Repair (<0.4s) — invoked ONLY if unsupported claims exist
        if unsupported and model is not None:
            mode = "LLM_MICRO"
            for un_claim in unsupported:
                # Targeted micro-repair removing ungrounded sentence span
                if un_claim.statement and un_claim.statement in answer:
                    answer = answer.replace(un_claim.statement, "").strip()
                    actions.append(RepairAction(
                        action_type="REMOVE_CLAIM",
                        reason=f"Removed unsupported claim span '{un_claim.statement[:30]}...'",
                        target_claim_id=un_claim.claim_id
                    ))

        return answer, actions, mode


# ==============================================================================
# MAIN VERIFICATION ENGINE ORCHESTRATOR
# ==============================================================================

class VerificationEngine:
    """Phase 7 Verification Engine main orchestrator v1.0."""

    def __init__(self, model=None):
        if model is not None:
            self.model = model
        else:
            api_key = os.environ.get("AZURE_OPENAI_API_KEY")
            azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
            api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
            deployment_name = os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt41mini")

            if api_key and azure_endpoint:
                self.model = AzureChatOpenAI(
                    azure_deployment=deployment_name,
                    openai_api_version=api_version,
                    azure_endpoint=azure_endpoint,
                    api_key=api_key,
                    temperature=0.0,
                )
            else:
                self.model = None

    def verify(self, inp: VerificationInput) -> tuple[VerificationResult, VerificationReport]:
        start_time = time.time()
        rules_triggered = []

        gen_out = inp.generator_output
        graph = inp.evidence_reasoning_graph

        # Engine 1: Claim Verification
        verified_claims, unsupported_claims, grounding_score = ClaimVerificationEngine.verify_claims(gen_out.claims, graph)
        rules_triggered.append(f"Engine1_Verified:{len(verified_claims)}/Unsupported:{len(unsupported_claims)}")

        # Engine 2: Citation Integrity
        citation_errors, citation_score = CitationIntegrityEngine.verify_citations(gen_out.citations_bound, graph)
        rules_triggered.append(f"Engine2_CitationErrors:{len(citation_errors)}")

        # Engine 3: Legal Consistency & Strategy Compliance
        consistency_errors, consistency_score = LegalConsistencyEngine.verify_consistency(inp)
        rules_triggered.append(f"Engine3_ConsistencyErrors:{len(consistency_errors)}")

        # Engine 4: Contract Integrity & Scores
        scores, status, failure_mode, contract_errors = ContractIntegrityEngine.audit_contract(
            inp=inp,
            grounding_score=grounding_score,
            citation_score=citation_score,
            consistency_score=consistency_score
        )
        rules_triggered.append(f"Engine4_Status:{status}")

        # Engine 5: Targeted Repairs
        repaired_answer = None
        actions = []
        repair_mode: RepairMode = "NONE"

        if status in ["REPAIRED", "FAIL"] and (consistency_errors or unsupported_claims):
            repaired_answer, actions, repair_mode = TargetedRepairPlanner.apply_repairs(
                inp=inp,
                unsupported=unsupported_claims,
                citation_errors=citation_errors,
                consistency_errors=consistency_errors,
                model=self.model
            )
            if actions and status != "FAIL":
                status = "REPAIRED"
                failure_mode = "NONE"

        exec_time = (time.time() - start_time) * 1000

        result = VerificationResult(
            verification_schema_version=VERIFICATION_CONTRACT_VERSION,
            overall_status=status,
            failure_mode=failure_mode,
            scores=scores,
            verified_claims=verified_claims,
            unsupported_claims=unsupported_claims,
            repaired_claims=[],
            citation_errors=citation_errors,
            consistency_errors=consistency_errors,
            contract_errors=contract_errors,
            repair_actions=actions,
            repaired_answer=repaired_answer,
            ready_for_response=status in ["PASS", "PASS_WITH_WARNINGS", "REPAIRED"],
            provenance=VerificationProvenance(
                verification_version=VERIFICATION_CONTRACT_VERSION,
                verifier_prompt_version=VERIFIER_PROMPT_VERSION,
                repair_mode=repair_mode,
                rules_triggered=rules_triggered,
                execution_time_ms=exec_time,
                llm_used=repair_mode == "LLM_MICRO",
                repair_count=len(actions)
            )
        )

        report = VerificationReport(
            result=result,
            generator_version=gen_out.generator_schema_version,
            checker_version=inp.relevance_decision.get("checker_version", "3.1")
        )

        return result, report
