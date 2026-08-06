# Architecture Specification: Certification Engine (v1.0)

## 1. Overview
The **Certification Engine (v1.0)** (`agents/certification_engine.py`) computes a cryptographic SHA256 audit checksum over response text, tenant scope, and retrieved vector chunk IDs to produce the `CertifiedResponse` payload.

---

## 2. Cryptographic Checksum Formula

$$\text{Checksum} = \text{SHA256}(\text{FormattedAnswer} \parallel \text{TenantID} \parallel \text{ThreadID} \parallel \text{SortedChunkIDs})$$

---

## 3. Public Contract Schema (`CertifiedResponse`)

```python
class CertifiedResponse(BaseModel):
    certification_schema_version: str = "1.0"
    checksum_sha256: str
    response_output: ResponseOutput
    audit_timestamp: str
    certification_status: Literal["CERTIFIED_VALID", "TAMPER_DETECTED"]
    provenance_hash: str
```

---

## 4. Inputs & Outputs
- **Inputs**: `response_output: ResponseOutput`, `documents: List[Document]`, `tenant_id: str`, `thread_id: str`
- **Outputs**: `CertifiedResponse` object.
