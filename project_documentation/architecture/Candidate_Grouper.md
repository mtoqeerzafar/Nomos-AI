# Architecture Specification: Candidate Grouper Engine

## 1. Overview
The **Candidate Grouper Engine** (`agents/candidate_grouper.py`) converts unstructured candidate chunk lists into relational statutory document hierarchies, grouping chunks by law number, law year, and canonical article key.

---

## 2. Core Hierarchy Layout

```
Document Codex (e.g. Ministerial Decision 471 of 1995)
  │
  ├── Article 4 (Penal Establishment Types)
  │     ├── Chunk 1 (Main Text)
  │     └── Chunk 2 (Executive Clauses)
  │
  └── Article 78 (Private School Inmate Education)
        └── Chunk 1 (Director Approval Condition)
```

---

## 3. Implementation Code

```python
class CandidateGrouperEngine:
    @staticmethod
    def group_candidates(documents: List[Document]) -> List[CandidateGroup]:
        groups = {}
        for doc in documents:
            meta = doc.metadata
            law_key = f"{meta.get('law_number', 'UNKNOWN')}_{meta.get('law_year', 'UNDATED')}"
            ...
```

---

## 4. Inputs & Outputs
- **Inputs**: `documents: List[Document]`
- **Outputs**: `List[CandidateGroup]` attached to `AgentState["candidate_groups"]`.
