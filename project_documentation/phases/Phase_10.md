# Phase 10 — Response Composer (Node 9) & Certification Authority (Node 10)

## 1. Background
Phase 10 represents the final presentation engineering and cryptographic certification layer of Nomos AI. Both engines execute **Zero LLM calls (0 API calls)** for 100% deterministic presentation and fail-closed audit protection.

---

## 2. Goals & Node Responsibilities

### A. Node 9: Response Composer Engine (`agents/composer.py`)
- **Manual**: [`Response_Composer.md`](file:///d:/RagnrAI/project_documentation/architecture/Response_Composer.md)
- **Role**: Executes 7 deterministic sub-engines (`ContractValidator`, `ResponseSelector`, `AnswerBuilder`, `CitationComposer`, `WarningComposer`, `MetadataComposer`, `OutputFormatter`).
- **Formatting**: Injects RTL unicode direction protection (`\u200f`), formats Arabic footnote citations, and renders outputs for multi-channel destinations (`MARKDOWN`, `STREAMING`, `API`, `TEAMS`, `SLACK`, `WHATSAPP`).

### B. Node 10: Certification Authority (`agents/certification_delivery.py`)
- **Manual**: [`Certification.md`](file:///d:/RagnrAI/project_documentation/architecture/Certification.md)
- **Role**: Executes 6 certification sub-engines, validates version matrix (`("1.1", "1.0", "1.0", "1.0")`), and computes an immutable SHA256 cryptographic checksum over canonical JSON (`_canonical_json`).
- **Output**: Issues tamper-evident `CertifiedResponse v1.0` payload and archives `CertificationRecord` audit record.

---

## 3. Architecture Node Mapping
- **Node Numbers**: **Node 9** & **Node 10**
- **Primary Code Location**: `agents/composer.py` & `agents/certification_delivery.py`
- **Output Contract**: `ResponseOutput v1.0` and `CertifiedResponse v1.0`.

---

## 4. Downstream Trajectory
Delivers final certified response payload to client application (Web UI, SSE Stream, REST API, Teams/Slack Adaptive Cards).
