import logging
import re
import json
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator
from utils.llm_factory import get_llm
from config.settings import settings

logger = logging.getLogger(__name__)

class IntentType(str, Enum):
    FACT_LOOKUP = "FACT_LOOKUP"
    SEMANTIC_QUESTION = "SEMANTIC_QUESTION"
    COMPARISON = "COMPARISON"
    HISTORICAL = "HISTORICAL"
    AMENDMENT = "AMENDMENT"

class QueryType(str, Enum):
    SINGLE_ENTITY = "single_entity"
    COMPARISON = "comparison"
    MULTI_HOP = "multi_hop"
    FOLLOW_UP = "follow_up"
    REFERENCE_QUERY = "reference_query"

class PlannerDecision(BaseModel):
    intent_type: IntentType = Field(description="Classification of the query intent.")
    query_type: QueryType = Field(default=QueryType.SINGLE_ENTITY, description="For legacy backwards compatibility.")
    needs_chat_history: bool = Field(description="True if the question refers to previous conversational context or asks about chat history.")
    needs_retrieval: bool = Field(description="True if the question asks for factual information, requires document retrieval, or needs external knowledge.")
    needs_query_expansion: bool = Field(description="True if the question needs rewriting, decomposition, or pronoun resolution.")
    document_ids_to_search: List[str] = Field(description="List of document IDs (UUIDs) to search. Leave empty to search all thread documents if no specific document is implied.")
    domains: List[str] = Field(default_factory=list, description="Domains inferred from the query (e.g. ['HR', 'Finance', 'Legal']).")
    target_date: Optional[str] = Field(None, description="Target effective date parsed from the query if any (e.g. '1992-12-15' or '1992').")
    output_language: str = Field(default="Arabic", description="Language for the final response (e.g. 'Arabic' or 'English').")
    strategy: str = Field(default="", description="Brief description of the search and reasoning strategy.")

    @model_validator(mode="after")
    def enforce_query_rules(self):
        # Enforce expansion for comparison, historical, and amendment intents
        if self.intent_type in {
            IntentType.COMPARISON,
            IntentType.HISTORICAL,
            IntentType.AMENDMENT
        }:
            self.needs_query_expansion = True

        # Map to legacy query_type for backwards compatibility
        if self.intent_type == IntentType.FACT_LOOKUP:
            self.query_type = QueryType.REFERENCE_QUERY
        elif self.intent_type == IntentType.SEMANTIC_QUESTION:
            self.query_type = QueryType.SINGLE_ENTITY
        elif self.intent_type == IntentType.COMPARISON:
            self.query_type = QueryType.COMPARISON
        else:
            self.query_type = QueryType.MULTI_HOP

        return self

class WorkflowPlanner:
    def __init__(self):
        """Initialize the Workflow Planner Agent."""
        self.model = get_llm(
            temperature=0.1,
            max_tokens=500,
            agent_name="planner"
        ).bind(response_format={"type": "json_object"})

    def plan(self, question: str, chat_history: str, attached_document_ids: List[str]) -> PlannerDecision:
        logger.info(f"WorkflowPlanner analyzing question: '{question}'")
        
        prompt = f"""
        You are the orchestration planner for an advanced RAG AI assistant.
        Analyze the user's question and determine the execution plan, domains, target date, and query classification.

        **Instructions:**
        1. Classify the query into one of these `intent_type`s:
           - "FACT_LOOKUP": Specific Article/Law/Clause lookup (e.g. "What does Article 9 say?", "read Section 12"). Prioritizes BM25.
           - "SEMANTIC_QUESTION": General QA/topic search (e.g. "Explain the rules for maternity leave"). Prioritizes dense embeddings.
           - "COMPARISON": Comparing two or more laws, versions, or concepts (e.g. "How does the 1992 law compare to 2020?").
           - "HISTORICAL": Tracing version lineages, evolution over time, or timelines.
           - "AMENDMENT": Checking amendments, resolutions, or how laws amended each other.
           
        2. Set `needs_chat_history` based on this semantic rule:
           Set `needs_chat_history = True` ONLY IF the user omits entities OR the meaning depends on previous turns. Do not use hardcoded lists.
        
        3. `needs_retrieval`: ALWAYS True UNLESS the user is just saying "hello", "thanks", "goodbye", or chatting completely casually.
        
        4. `needs_query_expansion`: True if the query needs rewriting or decomposition. Note: COMPARISON, HISTORICAL, and AMENDMENT always force query expansion.
        
        5. `document_ids_to_search`: If `Attached Document IDs` is NOT empty, output those exact IDs. Otherwise, leave empty.
        
        6. `domains`: Inferred domains from the query (e.g. ["Legal", "HR", "Finance", "Corporate"]).
        
        7. `target_date`: Inferred target effective date from the query if any (e.g. "1992-12-15" or "1992"). Otherwise null.
        
        8. `output_language`: "Arabic" or "English" (detect based on query language or requested language).
        
        9. `strategy`: A brief string describing the search strategy.

        **EXAMPLES OF CORRECT DECISIONS:**

        Example 1 (Fact Lookup):
        Conversation History: None
        Current Question: "What does Clause 17 of law 43 say?"
        Attached Document IDs: []
        Decision:
        {{
            "intent_type": "FACT_LOOKUP",
            "needs_query_expansion": false,
            "needs_chat_history": false,
            "needs_retrieval": true,
            "document_ids_to_search": [],
            "domains": ["Legal"],
            "target_date": null,
            "output_language": "English",
            "strategy": "Sparse key lookup for Article/Clause 17 in Law 43."
        }}

        Example 2 (Comparison):
        Conversation History: None
        Current Question: "Compare the penalties in the 1992 law vs the 2018 amendment."
        Attached Document IDs: []
        Decision:
        {{
            "intent_type": "COMPARISON",
            "needs_query_expansion": true,
            "needs_chat_history": false,
            "needs_retrieval": true,
            "document_ids_to_search": [],
            "domains": ["Legal"],
            "target_date": null,
            "output_language": "English",
            "strategy": "Retrieve penalties for both 1992 and 2018 versions and compare them."
        }}

        **CURRENT INPUT:**

        **User Question:** {question}
        
        **Available Chat History (if any):**
        {chat_history}
        
        **Currently Attached Document IDs:**
        {attached_document_ids}
        
        IMPORTANT: You MUST return ONLY a JSON object matching the schema exactly.
        """
        
        try:
            response = self.model.invoke(prompt)
            data = json.loads(response.content)
            
            # Deterministic language lock check to avoid prompt example bias
            latin_count = len(re.findall(r'[a-zA-Z]', question))
            arabic_count = len(re.findall(r'[\u0600-\u06FF]', question))
            if latin_count > arabic_count:
                data["output_language"] = "English"
            elif arabic_count > latin_count:
                data["output_language"] = "Arabic"

            decision = PlannerDecision(**data)
            logger.info(f"Planner Decision: {decision.model_dump()}")
            return decision
        except Exception as e:
            logger.error(f"Failed to plan workflow: {e}. Falling back to default decision.")
            
            target_lang = "English" if len(re.findall(r'[a-zA-Z]', question)) > len(re.findall(r'[\u0600-\u06FF]', question)) else "Arabic"
            # Fallback to a safe, general RAG behavior to prevent crashing
            return PlannerDecision(
                intent_type=IntentType.SEMANTIC_QUESTION,
                needs_chat_history=True,
                needs_retrieval=True,
                needs_query_expansion=True,
                output_language=target_lang,
                document_ids_to_search=attached_document_ids
            )
