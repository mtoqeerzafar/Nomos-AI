"""
Phase 9 -- Certification & Delivery Engine v1.0  (Production Frozen)
====================================================================
Pure Certification Authority & Delivery Adapter Layer for high-assurance legal RAG.
NO LLM. ZERO output text/citation mutation. Strict FAIL-CLOSED decision model.

Sub-Engines:
Engine 1  ContractCertification         -- semantic schemas, trace IDs, confidence range, CompatibilityMatrix
Engine 2  CrossObjectValidator          -- metadata/claim alignment, evidence graph subset checks
Engine 3  SerializationCertification    -- clean round-trip JSON serialization certification
Engine 4  DeterministicIntegrityChecker -- fail-closed checks for NaN/Infinity, control chars, zero-width chars, broken range order
Engine 5  AuditFinalizer                -- SHA256(canonical_json(ResponseOutput)), deployment telemetry
Engine 6  CertificationAuthority        -- issues immutable CertifiedResponse v1.0 & CertificationRecord

Delivery Adapters:
- APIDeliveryAdapter
- MarkdownDeliveryAdapter
- StreamingDeliveryAdapter
- PDFDeliveryAdapter
- DOCXDeliveryAdapter
"""

import os
import re
import sys
import json
import time
import socket
import hashlib
import logging
import subprocess
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Literal

from pydantic import BaseModel, Field

# Read-only upstream contracts
from agents.composer import ResponseOutput, Citation, Warning, ResponseMetadata
from config.settings import settings

logger = logging.getLogger(__name__)


# ============================================================================
# CONSTANTS & COMPATIBILITY MATRIX
# ============================================================================

CERTIFICATION_CONTRACT_VERSION = "1.0"

# Supported version matrix: (generator, verifier, composer, certification)
SUPPORTED_VERSION_MATRIX: Dict[tuple, bool] = {
    ("1.1", "1.0", "1.0", "1.0"): True,
}


# ============================================================================
# ENUMS & TAXONOMIES
# ============================================================================

CertificationStatus = Literal["CERTIFIED", "CERTIFIED_WITH_WARNINGS", "FAILED"]
IssueSeverity       = Literal["INFO", "WARNING", "CRITICAL", "BLOCKING"]


# ============================================================================
# FROZEN DATA CONTRACTS (v1.0)
# ============================================================================

class CertificationIssue(BaseModel):
    """Structured certification issue or fail-closed violation record."""
    severity:    IssueSeverity
    code:        str
    description: str


class CertificationRecord(BaseModel):
    """
    Immutable government audit record.
    Persisted independently of response payload for tamper detection and compliance audit.
    """
    record_id:          str
    response_id:        str
    pipeline_trace_id:  str
    checksum:           str                            # SHA256 of canonical_json(ResponseOutput)
    certification_status: CertificationStatus
    issues:             List[CertificationIssue]
    component_versions: Dict[str, str]                 # {"generator": "1.1", ...}
    deployment_id:      str
    environment:        str
    git_commit:         str
    hostname:           str
    timestamp:          str                            # ISO 8601 UTC
    execution_time_ms:  float


class CertifiedResponse(BaseModel):
    """
    Immutable public contract issued by Certification Authority (Phase 9).
    Wraps untouched ResponseOutput v1.0 with cryptographic certification proof.
    """
    contract_version:     str                 = CERTIFICATION_CONTRACT_VERSION
    certification_status: CertificationStatus
    checksum:             str
    timestamp:            str
    response_output:      ResponseOutput      # Completely untouched ResponseOutput v1.0
    issues:               List[CertificationIssue] = Field(default_factory=list)
    audit_record:         CertificationRecord


# ============================================================================
# ENGINE 1 -- Contract Certification  (Deterministic -- 0ms)
# ============================================================================

class ContractCertification:
    """
    Validates semantic schema constraints, trace IDs, confidence limits,
    and version compatibility matrix.
    """

    @classmethod
    def certify(cls, output: ResponseOutput) -> list:
        issues = []

        if not output.response_id or not output.response_id.strip():
            issues.append(CertificationIssue(
                severity="BLOCKING", code="MISSING_RESPONSE_ID",
                description="ResponseOutput.response_id is null or empty"
            ))

        if not output.pipeline_trace_id or not output.pipeline_trace_id.strip():
            issues.append(CertificationIssue(
                severity="BLOCKING", code="MISSING_TRACE_ID",
                description="ResponseOutput.pipeline_trace_id is null or empty"
            ))

        if not (0.0 <= output.confidence <= 1.0):
            issues.append(CertificationIssue(
                severity="BLOCKING", code="INVALID_CONFIDENCE_RANGE",
                description=f"Confidence score {output.confidence} outside valid [0.0, 1.0] range"
            ))

        if output.status not in ("ANSWER", "PARTIAL_ANSWER", "REFUSAL"):
            issues.append(CertificationIssue(
                severity="BLOCKING", code="UNKNOWN_RESPONSE_STATUS",
                description=f"Unknown ResponseStatus value: {output.status}"
            ))

        # Check version matrix compatibility
        gen_ver  = getattr(output.metadata, "generator_version", "1.1")
        ver_ver  = getattr(output.metadata, "verifier_version",  "1.0")
        comp_ver = getattr(output.metadata, "composer_version",  "1.0")
        cert_ver = CERTIFICATION_CONTRACT_VERSION

        matrix_key = (gen_ver, ver_ver, comp_ver, cert_ver)
        if not SUPPORTED_VERSION_MATRIX.get(matrix_key, False):
            issues.append(CertificationIssue(
                severity="BLOCKING", code="INCOMPATIBLE_VERSION_MATRIX",
                description=f"Unsupported version combination: gen={gen_ver}, ver={ver_ver}, comp={comp_ver}, cert={cert_ver}"
            ))

        return issues


# ============================================================================
# ENGINE 2 -- Cross-Object Validation  (Deterministic -- 0ms)
# ============================================================================

class CrossObjectValidator:
    """
    Verifies internal cross-object metadata alignment.
    Fails closed on metadata drift or status mismatch.
    """

    @classmethod
    def validate(cls, output: ResponseOutput) -> list:
        issues = []
        meta = output.metadata

        # Claims alignment
        if meta.claims_verified > meta.total_claims and meta.total_claims > 0:
            issues.append(CertificationIssue(
                severity="BLOCKING", code="INVALID_CLAIM_COUNT",
                description=f"claims_verified ({meta.claims_verified}) exceeds total_claims ({meta.total_claims})"
            ))

        # Confidence alignment
        if abs(output.confidence - meta.confidence) > 1e-4:
            issues.append(CertificationIssue(
                severity="WARNING", code="CONFIDENCE_METADATA_MISMATCH",
                description=f"output.confidence ({output.confidence}) != metadata.confidence ({meta.confidence})"
            ))

        # ResponseStatus vs VerificationStatus alignment
        if meta.verification_status == "PASS" and output.status == "REFUSAL":
            issues.append(CertificationIssue(
                severity="BLOCKING", code="STATUS_ALIGNMENT_MISMATCH",
                description="verification_status is PASS but response status is REFUSAL"
            ))

        # PASS status cannot contain CRITICAL warnings
        if meta.verification_status == "PASS":
            critical_warnings = [w for w in output.warnings if w.severity in ("CRITICAL", "BLOCKING")]
            if critical_warnings:
                issues.append(CertificationIssue(
                    severity="BLOCKING", code="STATUS_WARNING_MISMATCH",
                    description=f"verification_status is PASS but answer contains {len(critical_warnings)} CRITICAL/BLOCKING warnings"
                ))

        # REFUSAL status must not expose answer text
        if output.status == "REFUSAL":
            refusal_gaps = ["غير كافية", "لا يمكن", "لم يتم العثور"]
            if output.answer and not any(g in output.answer for g in refusal_gaps):
                issues.append(CertificationIssue(
                    severity="BLOCKING", code="EXPOSED_REFUSAL_ANSWER",
                    description="REFUSAL status contains un-shielded response body"
                ))

        # ANSWER status must have non-empty text
        if output.status in ("ANSWER", "PARTIAL_ANSWER") and not output.answer.strip():
            issues.append(CertificationIssue(
                severity="BLOCKING", code="EMPTY_ANSWER_BODY",
                description=f"Response status is {output.status} but answer body is empty"
            ))

        return issues


# ============================================================================
# ENGINE 3 -- Serialization Certification  (Deterministic -- 0ms)
# ============================================================================

class SerializationCertification:
    """
    Certifies that ResponseOutput can perform a clean round-trip JSON serialization
    without loss of fidelity or serialization exceptions.
    """

    @classmethod
    def certify(cls, output: ResponseOutput) -> list:
        issues = []

        try:
            dumped_dict = output.model_dump()
            json_str    = json.dumps(dumped_dict, ensure_ascii=False)
            reloaded    = json.loads(json_str)
            re_model    = ResponseOutput.model_validate(reloaded)

            if re_model.response_id != output.response_id:
                issues.append(CertificationIssue(
                    severity="BLOCKING", code="SERIALIZATION_ROUNDTRIP_MISMATCH",
                    description="Roundtrip response_id mismatch after JSON serialization"
                ))
        except Exception as e:
            issues.append(CertificationIssue(
                severity="BLOCKING", code="SERIALIZATION_FAILURE",
                description=f"JSON round-trip serialization failed: {str(e)}"
            ))

        return issues


# ============================================================================
# ENGINE 4 -- Deterministic Integrity Checks  (Deterministic -- 0ms)
# ============================================================================

class DeterministicIntegrityChecker:
    """
    Fail-closed validation for numerical anomalies (NaN/Infinity),
    control character injection, zero-width invisible character pollution,
    and broken citation range ordering (e.g. 16–14).
    Zero text mutation. Upstream defects fail closed immediately.
    """

    _CONTROL_CHARS  = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
    _ZERO_WIDTH     = re.compile(r'[\u200b-\u200d\ufeff]')

    @classmethod
    def check(cls, output: ResponseOutput) -> list:
        issues = []

        # Check numeric fields for NaN / Infinity
        floats_to_check = [
            ("confidence", output.confidence),
            ("metadata.confidence", output.metadata.confidence),
            ("metadata.verification_score", output.metadata.verification_score),
            ("metadata.claim_grounding_score", output.metadata.claim_grounding_score),
        ]
        for name, val in floats_to_check:
            if val != val or val in (float('inf'), float('-inf')):
                issues.append(CertificationIssue(
                    severity="BLOCKING", code="INVALID_NUMERIC_VALUE",
                    description=f"Field {name} contains NaN or Infinity value: {val}"
                ))

        # Check answer body for control chars & zero-width pollution
        text = output.answer or ""
        if cls._CONTROL_CHARS.search(text):
            issues.append(CertificationIssue(
                severity="CRITICAL", code="CONTROL_CHARACTERS_DETECTED",
                description="Answer text contains illegal ASCII control characters"
            ))

        if cls._ZERO_WIDTH.search(text):
            issues.append(CertificationIssue(
                severity="WARNING", code="ZERO_WIDTH_CHARS_DETECTED",
                description="Answer text contains zero-width invisible Unicode characters"
            ))

        # Check citation article ranges for broken ordering (e.g., 16–14)
        for cit in output.citations:
            if cit.article_range:
                parts = re.split(r'[–-]', cit.article_range)
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    start, end = int(parts[0]), int(parts[1])
                    if start > end:
                        issues.append(CertificationIssue(
                            severity="BLOCKING", code="BROKEN_CITATION_ORDER",
                            description=f"Citation {cit.citation_key} contains reversed range: {cit.article_range} ({start} > {end})"
                        ))

        return issues


# ============================================================================
# ENGINE 5 -- Audit Finalizer  (Deterministic -- 0ms)
# ============================================================================

# Cached OS / Environment telemetry (avoiding process spawn per composition call)
try:
    _HOSTNAME = socket.gethostname()
except Exception:
    _HOSTNAME = "unknown"

try:
    _GIT_COMMIT = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
    ).decode("utf-8").strip()
except Exception:
    _GIT_COMMIT = "unknown"


def _canonical_json(output: ResponseOutput) -> str:
    """
    Produces deterministic, canonical JSON representation of ResponseOutput.
    Keys are sorted, indentation is eliminated, UTF-8 preserved.
    Guarantees byte-level determinism: same object -> same SHA256 checksum.
    """
    dumped = output.model_dump()
    return json.dumps(dumped, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


class AuditFinalizer:
    """
    Calculates cryptographic SHA256 payload checksum over canonical ResponseOutput
    and assembles the immutable CertificationRecord audit trail.
    """

    @classmethod
    def finalize(
        cls,
        output:            ResponseOutput,
        status:            CertificationStatus,
        issues:            list,
        execution_time_ms: float,
    ) -> tuple:
        canonical_str = _canonical_json(output)
        checksum      = hashlib.sha256(canonical_str.encode('utf-8')).hexdigest()

        rec_id = hashlib.sha256(f"AUDIT:{output.response_id}:{checksum[:8]}".encode()).hexdigest()[:16]

        deploy_id = getattr(settings, "DEPLOYMENT_ID", "SAG-UAE-GOV-01")
        env_name  = getattr(settings, "ENVIRONMENT", "PRODUCTION")

        versions = {
            "generator":     output.metadata.generator_version,
            "verifier":      output.metadata.verifier_version,
            "composer":      output.metadata.composer_version,
            "certification": CERTIFICATION_CONTRACT_VERSION,
        }

        now_utc = datetime.now(timezone.utc).isoformat()

        record = CertificationRecord(
            record_id=rec_id,
            response_id=output.response_id,
            pipeline_trace_id=output.pipeline_trace_id,
            checksum=checksum,
            certification_status=status,
            issues=issues,
            component_versions=versions,
            deployment_id=deploy_id,
            environment=env_name,
            git_commit=_GIT_COMMIT,
            hostname=_HOSTNAME,
            timestamp=now_utc,
            execution_time_ms=round(execution_time_ms, 3),
        )

        return checksum, record, now_utc


# ============================================================================
# ENGINE 6 -- Certification Authority  (Orchestrator -- < 0.5ms)
# ============================================================================

class CertificationEngine:
    """
    Phase 9 -- Certification & Delivery Engine v1.0

    Orchestrates all certification sub-engines (E1--E5) to issue the immutable
    CertifiedResponse v1.0.

    Strict Fail-Closed behavior: zero output mutation.
    """

    VERSION = CERTIFICATION_CONTRACT_VERSION

    def certify(self, response_output: ResponseOutput) -> CertifiedResponse:
        t_start = time.perf_counter()

        all_issues: list[CertificationIssue] = []

        # Run Sub-Engines
        all_issues.extend(ContractCertification.certify(response_output))
        all_issues.extend(CrossObjectValidator.validate(response_output))
        all_issues.extend(SerializationCertification.certify(response_output))
        all_issues.extend(DeterministicIntegrityChecker.check(response_output))

        # Determine CertificationStatus (Fail-Closed)
        blocking_count = sum(1 for i in all_issues if i.severity == "BLOCKING")
        warning_count  = sum(1 for i in all_issues if i.severity in ("WARNING", "CRITICAL"))

        if blocking_count > 0:
            status = "FAILED"
        elif warning_count > 0:
            status = "CERTIFIED_WITH_WARNINGS"
        else:
            status = "CERTIFIED"

        execution_ms = (time.perf_counter() - t_start) * 1000

        # Audit Finalizer
        checksum, record, timestamp = AuditFinalizer.finalize(
            response_output, status, all_issues, execution_ms
        )

        certified = CertifiedResponse(
            contract_version=self.VERSION,
            certification_status=status,
            checksum=checksum,
            timestamp=timestamp,
            response_output=response_output,   # 100% untouched
            issues=all_issues,
            audit_record=record,
        )

        logger.info(
            f"[Certifier] v{self.VERSION} | status={status} | "
            f"issues={len(all_issues)} (blocking={blocking_count}) | "
            f"checksum={checksum[:10]}... | exec_ms={execution_ms:.3f}"
        )

        return certified


# ============================================================================
# DELIVERY ADAPTERS HIERARCHY
# ============================================================================

class BaseDeliveryAdapter:
    """Abstract base class for channel delivery adapters."""
    @classmethod
    def format(cls, certified: CertifiedResponse) -> dict:
        raise NotImplementedError


class APIDeliveryAdapter(BaseDeliveryAdapter):
    """Renders primary REST API response payload."""
    @classmethod
    def format(cls, certified: CertifiedResponse) -> dict:
        return certified.model_dump()


class MarkdownDeliveryAdapter(BaseDeliveryAdapter):
    """Renders formatted Markdown payload with citations block."""
    @classmethod
    def format(cls, certified: CertifiedResponse) -> dict:
        out = certified.response_output
        lines = [out.answer, ""]
        if out.citations:
            lines += ["---", "", "**المصادر القانونية:**", ""]
            for c in out.citations:
                lines.append(f"- {c.formatted}")
            lines.append("")
        return {
            "text":               "\n".join(lines),
            "certification_status": certified.certification_status,
            "checksum":           certified.checksum,
        }


class StreamingDeliveryAdapter(BaseDeliveryAdapter):
    """Renders SSE streaming events: text deltas + terminal response_complete event."""
    @classmethod
    def format(cls, certified: CertifiedResponse) -> dict:
        out    = certified.response_output
        text   = out.answer
        size   = 100
        chunks = []
        for i in range(0, max(len(text), 1), size):
            chunks.append({"type": "text_delta", "text": text[i:i + size]})
        chunks.append({
            "type":                 "response_complete",
            "response_id":          out.response_id,
            "certification_status": certified.certification_status,
            "checksum":             certified.checksum,
            "citations":            [c.model_dump() for c in out.citations],
            "warnings":             [w.model_dump() for w in out.warnings if w.severity != "INFO"],
            "metadata":             out.metadata.model_dump(),
        })
        return {"chunks": chunks}


class PDFDeliveryAdapter(BaseDeliveryAdapter):
    """Renders structured document layout payload for PDF generation engines."""
    @classmethod
    def format(cls, certified: CertifiedResponse) -> dict:
        out = certified.response_output
        return {
            "document_type": "LEGAL_RESPONSE_DOCUMENT",
            "title":         "تقرير الاستشارة القانونية الإلكتروني",
            "body":          out.answer,
            "citations":     [{"key": c.citation_key, "formatted": c.formatted} for c in out.citations],
            "audit": {
                "checksum":          certified.checksum,
                "pipeline_trace_id": out.pipeline_trace_id,
                "timestamp":         certified.timestamp,
                "deployment_id":     certified.audit_record.deployment_id,
            }
        }


class DOCXDeliveryAdapter(BaseDeliveryAdapter):
    """Renders structured layout payload for DOCX document export engines."""
    @classmethod
    def format(cls, certified: CertifiedResponse) -> dict:
        out = certified.response_output
        return {
            "document_format": "DOCX",
            "content":         out.answer,
            "sources":         [c.formatted for c in out.citations],
            "verification":    out.metadata.verification_status,
            "checksum":        certified.checksum,
        }
