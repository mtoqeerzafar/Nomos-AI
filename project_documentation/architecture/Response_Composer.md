# Architecture Specification: Response Composer (v1.0)

## 1. Overview
The **Response Composer (v1.0)** (`agents/response_composer.py`) aggregates verified generator outputs, deduplicates citations, formats disclaimers, and constructs the public contract `ResponseOutput` object.

---

## 2. Public Contract Schema (`ResponseOutput`)

```python
class ResponseOutput(BaseModel):
    response_schema_version: str = "1.0"
    answer_status: Literal["ANSWER", "REFUSAL", "PARTIAL_ANSWER"]
    formatted_answer: str
    citations: List[str]
    warnings_and_disclaimers: List[str]
    confidence_score: float
    verification_passed: bool
    telemetry: Dict[str, Any]
```

---

## 3. Inputs & Outputs
- **Inputs**: `generator_output: GeneratorOutput`, `verification_report: VerificationReport`
- **Outputs**: `ResponseOutput` instance.
