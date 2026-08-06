# =============================================================================
# DEPRECATED — as of Phase 7 migration (agents/verifier.py v1.0)
# VerificationAgent is NO LONGER wired into agents/workflow.py.
# Superseded by: agents/verifier.py :: VerificationEngine
# Kept for reference only. Do not delete until 1 week of clean production runs.
# =============================================================================
import json
from typing import Dict, List, Literal
from langchain_core.documents import Document
from utils.llm_factory import get_llm
from config.settings import settings
from pydantic import BaseModel, Field

# Define Verification Schema
class VerificationResult(BaseModel):
    Supported: Literal["YES", "NO"] = Field(description="Is the answer supported by the context?")
    Unsupported_Claims: List[str] = Field(description="List of unsupported claims", alias="Unsupported Claims")
    Contradictions: List[str] = Field(description="List of contradictions")
    Relevant: Literal["YES", "NO"] = Field(description="Is the answer relevant to the question?")
    Additional_Details: str = Field(description="Any additional details or explanations", alias="Additional Details")
    Failure_Reason: Literal["NONE", "MISSING_EVIDENCE", "WRONG_REASONING", "NO_ANSWER_IN_DOC"] = Field(description="The primary reason for failure if Supported is NO", alias="Failure Reason")

class VerificationAgent:
    def __init__(self):
        """
        Initialize the verification agent with configured LLM.
        """
        print("Initializing VerificationAgent...")
        
        self.model = get_llm(
            temperature=0.0,
            max_tokens=1500,
            agent_name="verifier"
        ).bind(response_format={"type": "json_object"})
        
        print("VerificationAgent model initialized successfully.")

    def sanitize_response(self, response_text: str) -> str:
        return response_text.strip()

    def generate_prompt(self, answer: str, context: str) -> str:
        prompt = f"""
        You are an AI assistant designed to verify the accuracy and relevance of answers based on the provided context.

        **Instructions:**
        - Verify the following answer against the provided context.
        - Respond strictly with a JSON object.
        - Check for:
        1. Direct/indirect factual support (YES/NO). Accept INDIRECT support: if the answer is a reasonable synthesis or interpretation of the context, mark Supported as YES.
        2. Unsupported claims (list only claims that are clearly contradicted by or absent from the context — do NOT list inferences or reasonable summaries).
        3. Contradictions (list any if present)
        4. Relevance to the question (YES/NO)
        5. If Supported is NO, identify the failure reason from these options: MISSING_EVIDENCE, WRONG_REASONING, NO_ANSWER_IN_DOC.

        **Important rules for Arabic legal text:**
        - The context may have minor spacing or encoding artifacts from PDF extraction. Do NOT mark as MISSING_EVIDENCE just because exact phrasing differs.
        - If the answer correctly describes what the law says, even if not word-for-word, mark Supported as YES.
        - Only mark Supported as NO if the answer makes a claim that is clearly absent from or contradicted by the context.
        
        **JSON Format Expected:**
        {{
            "Supported": "YES" or "NO",
            "Unsupported Claims": ["item1", "item2"],
            "Contradictions": ["item1", "item2"],
            "Relevant": "YES" or "NO",
            "Additional Details": "Any extra information or explanations",
            "Failure Reason": "NONE" or "MISSING_EVIDENCE" or "WRONG_REASONING" or "NO_ANSWER_IN_DOC"
        }}

        **Answer:** {answer}
        **Context:**
        {context}
        """
        return prompt

    def parse_verification_response(self, response_text: str) -> Dict:
        try:
            verification = json.loads(response_text)
            for key in ["Supported", "Unsupported Claims", "Contradictions", "Relevant", "Additional Details", "Failure Reason"]:
                if key not in verification:
                    if key in {"Unsupported Claims", "Contradictions"}:
                        verification[key] = []
                    elif key in {"Additional Details"}:
                        verification[key] = ""
                    elif key == "Failure Reason":
                        verification[key] = "NONE"
                    else:
                        verification[key] = "NO"
            return verification
        except Exception as e:
            print(f"Error parsing verification response: {e}")
            return None

    def format_verification_report(self, verification: Dict) -> str:
        supported = verification.get("Supported", "NO")
        unsupported_claims = verification.get("Unsupported Claims", [])
        contradictions = verification.get("Contradictions", [])
        relevant = verification.get("Relevant", "NO")
        additional_details = verification.get("Additional Details", "")
        failure_reason = verification.get("Failure Reason", "NONE")

        report = f"**Supported:** {supported}\n"
        if unsupported_claims:
            report += f"**Unsupported Claims:** {', '.join(unsupported_claims)}\n"
        else:
            report += f"**Unsupported Claims:** None\n"

        if contradictions:
            report += f"**Contradictions:** {', '.join(contradictions)}\n"
        else:
            report += f"**Contradictions:** None\n"

        report += f"**Relevant:** {relevant}\n"
        if additional_details:
            report += f"**Additional Details:** {additional_details}\n"
        else:
            report += f"**Additional Details:** None\n"
            
        report += f"**Failure Reason:** {failure_reason}\n"
        return report

    def check(self, answer: str, documents: List[Document], planner_decision: Dict = None) -> Dict:
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"VerificationAgent.check called with answer of length {len(answer)} and {len(documents)} documents.")
        context = "\n\n".join([doc.page_content for doc in documents])
        prompt = self.generate_prompt(answer, context)

        try:
            logger.info("Sending verification prompt to the model...")
            response = self.model.invoke(prompt)
            llm_response = response.content
            if not llm_response:
                logger.warning("Groq verifier returned no text content.")
            logger.info("Verifier LLM response received.")
        except Exception as e:
            logger.error(f"Error during verifier model inference: {e}")
            raise RuntimeError("Failed to verify answer due to a model error.") from e

        sanitized_response = self.sanitize_response(llm_response) if llm_response else ""
        if not sanitized_response:
            verification_report = {
                "Supported": "NO",
                "Unsupported Claims": [],
                "Contradictions": [],
                "Relevant": "NO",
                "Additional Details": "Empty response from the model.",
                "Failure Reason": "NONE"
            }
        else:
            verification_report = self.parse_verification_response(sanitized_response)
            if verification_report is None:
                verification_report = {
                    "Supported": "NO",
                    "Unsupported Claims": [],
                    "Contradictions": [],
                    "Relevant": "NO",
                    "Additional Details": "Failed to parse the model's response.",
                    "Failure Reason": "NONE"
                }

        # Language Locking check
        target_lang = (planner_decision or {}).get("output_language", "Arabic")
        # Simple heuristic to detect if response contains characters from the target language
        has_arabic = any(1536 <= ord(c) <= 1791 for c in answer)
        has_english = any(65 <= ord(c) <= 90 or 97 <= ord(c) <= 122 for c in answer)
        
        lang_mismatch = False
        mismatch_reason = ""
        if target_lang == "Arabic" and not has_arabic:
            lang_mismatch = True
            mismatch_reason = "Expected Arabic response, but response does not contain Arabic script."
        elif target_lang == "English" and not has_english:
            lang_mismatch = True
            mismatch_reason = "Expected English response, but response does not contain English text."
            
        if lang_mismatch:
            logger.warning(f"[Language Lock] Mismatch detected: {mismatch_reason}")
            verification_report["Supported"] = "NO"
            verification_report["Failure Reason"] = "WRONG_REASONING"
            if "Unsupported Claims" not in verification_report:
                verification_report["Unsupported Claims"] = []
            verification_report["Unsupported Claims"].append(f"Language lock mismatch: {mismatch_reason}")
            verification_report["Additional Details"] = (
                verification_report.get("Additional Details", "") + f" [Language Mismatch: {mismatch_reason}]"
            )

        verification_report_formatted = self.format_verification_report(verification_report)
        # Log at WARNING level so it appears in workflow.log for debugging
        import logging as _log
        _wf_logger = _log.getLogger("agents.workflow")
        _wf_logger.warning(
            f"[VERIFIER] Supported={verification_report.get('Supported')} "
            f"Relevant={verification_report.get('Relevant')} "
            f"Reason={verification_report.get('Failure Reason')} "
            f"Details={verification_report.get('Additional Details', '')[:200]}"
        )

        return {
            "verification_report": verification_report_formatted,
            "failure_reason": verification_report.get("Failure Reason", "NONE"),
            "context_used": context
        }
