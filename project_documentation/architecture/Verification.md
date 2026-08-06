# Architecture Specification: Verification Engine (v1.0)

## 1. Overview
The **Verification Engine (v1.0)** (`agents/verification_agent.py`) is the zero-hallucination circuit breaker. It evaluates every generated claim against 7 deterministic factual verification gates before response delivery.

---

## 2. The 7 Verification Gates

1. **Gate 1: Text Support**: Verifies claim against node text.
2. **Gate 2: Citation Binding**: Validates article key metadata.
3. **Gate 3: Language Lock**: Confirms target output language match.
4. **Gate 4: Contradiction Check**: Identifies text conflicts.
5. **Gate 5: Scope & Completeness**: Audits query coverage.
6. **Gate 6: Amendment Status**: Checks for superseded laws.
7. **Gate 7: Entity Lock**: Confirms law and organ names.

---

## 3. Support Score Formula

$$\text{Support Score} = \frac{\text{Count of Supported Claims}}{\text{Total Claims Generated}}$$

- If $\text{Support Score} \ge 0.70 \rightarrow$ `PASS`
- If $0.50 \le \text{Support Score} < 0.70 \rightarrow$ `WARNING` (Attaches disclaimers)
- If $\text{Support Score} < 0.50 \rightarrow$ `FAIL` (Strips answer, triggers Refusal)

---

## 4. Inputs & Outputs
- **Inputs**: `generator_output: GeneratorOutput`, `evidence_graph: EvidenceReasoningGraph`
- **Outputs**: `VerificationReport` object.
