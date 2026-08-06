# =============================================================================
# DEPRECATED — as of Phase 6 migration (agents/generator.py v1.1)
# ResearchAgent is NO LONGER wired into agents/workflow.py.
# Superseded by: agents/generator.py :: GeneratorAgent
# Kept for reference only. Do not delete until 1 week of clean production runs.
# =============================================================================
from typing import Dict, List, Optional
from langchain_core.documents import Document
from utils.llm_factory import get_llm
from config.settings import settings
import json
import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

class OutputFormat(Enum):
    DEFAULT = "default"
    BULLETS = "bullets"
    COMPARISON = "comparison"
    TABLE = "table"
    TIMELINE = "timeline"
    STEP_BY_STEP = "step_by_step"
    SUMMARY = "summary"

@dataclass
class GenerationContext:
    language: str = "Arabic"
    intent: str = "factual"
    output_format: OutputFormat = OutputFormat.DEFAULT
    prompt_version: str = "research_v6"

class ResearchAgent:
    def __init__(self):
        """
        Initialize the research agent with configured LLM.
        """
        print("Initializing ResearchAgent...")
        
        self.model = get_llm(
            temperature=0.3,
            max_tokens=2048,
            agent_name="researcher"
        )
        
        print("ResearchAgent model initialized successfully.")

    def sanitize_response(self, response_text: str) -> str:
        """
        Sanitize the LLM's response by stripping unnecessary whitespace.
        """
        return response_text.strip()

    def generate_prompt(self, question: str, context: str, feedback: str = None, chat_history: str = "", conflicts: List[dict] = None, gen_context: GenerationContext = None) -> str:
        """
        Generate a structured prompt for the LLM to generate a precise and factual answer.
        """
        prompt = f"""
        You are an AI assistant designed to provide precise and factual answers based on the given context.
        """
        
        if gen_context:
            prompt += f"""
        **Output Requirements:**
        - Language: You MUST reply in {gen_context.language}. This is an absolute requirement.
        """
            
            if gen_context.output_format == OutputFormat.COMPARISON:
                prompt += """
        - Structure: The user requested a comparison or structured output. Please organize your answer into clear, structured sections (e.g. using bullet points or a table) detailing the similarities and differences, or the respective roles of the entities compared.
        """
                
        prompt += f"""
        **Instructions:**
        1. Read the provided context carefully.
        2. Before writing the final answer, write out your reasoning steps inside `<thinking>` and `</thinking>` tags.
        3. After the `</thinking>` block, provide the final factual answer.
        4. Answer the following question using only the provided context.
        5. Be clear, concise, and factual.
        6. When extracting details from a specific section, extract **every single instance** where the target keyword appears within that section's boundary. Do not skip or filter out steps based on localized phrasing.
        7. Explicitly ground your claims in the provided context, mentioning which document or article the information comes from where relevant.

        ### CRITICAL BOUNDARY ENFORCEMENT:
        1. You are a literal extraction engine. You are FORBIDDEN from using logic, reasoning, or analogy to decide if two different workflows are "similar" or "applicable."
        2. If a query asks about a specific named procedure (e.g., "Resignation"), your search space is strictly isolated to that specific text block or table.
        3. If a step appears under a different procedure heading (such as "Termination of Service" or "Retirement"), you must completely ignore it, even if it contains identical keywords or performs a similar administrative function.
        4. Do not rationalize or infer cross-procedure workflows. However, if the user asks about the sequence of events (e.g., "What happens before X?"), you are permitted and encouraged to logically connect the sequential steps that are clearly outlined in the context.
        """
        if conflicts:
            prompt += f"""
        ### CRITICAL AUTHORITY & CONFLICT ALERT:
        We detected the following version or authority conflicts in the source documents. You must apply these resolution rules:
        - Federal Laws override Executive Regulations, Cabinet Resolutions, and Ministerial Resolutions.
        - Cabinet Decisions override Ministerial Resolutions.
        - Newer active versions of the same law override superseded/older versions.
        
        **Conflicts List:**
        """
            for c in conflicts:
                prompt += f"        - {c.get('description')}\n"
            prompt += "\n"

        if chat_history:
            prompt += f"""
        **Previous Conversation History:**
        {chat_history}
        """

        if feedback:
            prompt += f"""
        - PREVIOUS ATTEMPT FEEDBACK: The following feedback was provided on a previous draft. Please address these issues in your reasoning and answer:
        {feedback}
        """

        prompt += f"""
        **Question:** {question}
        
        **Context (Grouped by Source Provenance):**
        {context}

        **Provide your answer below:**
        """
        logger.debug(f"Generated prompt: {prompt}")
        return prompt

    def generate(self, question: str, documents: List[Document], feedback: str = None, chat_history: str = "", conflicts: List[dict] = None, gen_context: GenerationContext = None) -> Dict:
        """
        Generate an initial answer using the provided documents.
        """
        logger.info(f"ResearchAgent.generate called with question='{question}' and {len(documents)} documents.")

        # Group documents by source provenance origin
        grouped_docs = {}
        for doc in documents:
            source = doc.metadata.get("relation_source") or doc.metadata.get("title") or doc.metadata.get("source") or "Unknown Document"
            if source not in grouped_docs:
                grouped_docs[source] = []
            grouped_docs[source].append(doc)
            
        context_parts = []
        for source, docs in grouped_docs.items():
            context_parts.append(f"### Source Document: {source}")
            for idx, doc in enumerate(docs):
                score = doc.metadata.get("rerank_score") or doc.metadata.get("vector_score")
                score_str = f" (Score: {score:.2f})" if score is not None else ""
                
                # Check for conflict notes or graph expansion provenance
                notes = []
                if doc.metadata.get("graph_expansion"):
                    rel_type = doc.metadata.get("relation_type", "related")
                    notes.append(f"Expanded via {rel_type} relation")
                if doc.metadata.get("version_conflict"):
                    notes.append("VERSION CONFLICT")
                if doc.metadata.get("lifecycle_status") == "Superseded":
                    notes.append("SUPERSEDED VERSION")
                if doc.metadata.get("authority_warning"):
                    notes.append(doc.metadata.get("authority_warning"))
                if doc.metadata.get("hierarchy_note"):
                    notes.append(doc.metadata.get("hierarchy_note"))
                    
                note_str = f" [{', '.join(notes)}]" if notes else ""
                context_parts.append(f"  [Chunk {idx+1}]{score_str}{note_str}:")
                context_parts.append(f"  {doc.page_content}")
                context_parts.append("  " + "-" * 40)
            context_parts.append("\n")
            
        context = "\n".join(context_parts)
        logger.info(f"Combined grouped context length: {len(context)} characters.")

        # Create a prompt for the LLM
        prompt = self.generate_prompt(question, context, feedback, chat_history, conflicts, gen_context)
        logger.info("Prompt created with grouped context.")

        # Call the model to generate the answer
        try:
            response = self.model.invoke(prompt)
            llm_response = response.content
            if not llm_response:
                logger.warning("Groq returned no text content.")
        except Exception as e:
            logger.error(f"Error during model inference: {e}")
            raise RuntimeError(f"Research agent model inference failed: {e}") from e

        # Sanitize the response
        draft_answer = self.sanitize_response(llm_response) if llm_response else "I cannot answer this question based on the provided documents."

        logger.info(f"Generated draft answer length: {len(draft_answer)}")

        return {
            "draft_answer": draft_answer,
            "context_used": context
        }
