import logging
from typing import TypedDict, List, Dict
from langchain_core.messages import AIMessage
from langgraph.graph import StateGraph, END
from langchain_core.documents import Document

from .generator import GeneratorAgent, EvidenceReasoningGraphBuilder, VerificationInput
from .verifier import VerificationEngine
from .composer import ResponseComposer, ResponseOutput
from .certification_delivery import CertificationEngine, CertifiedResponse
from .relevance_checker import RelevanceChecker
from .query_rewriter import QueryRewriter
from .planner import WorkflowPlanner
from .reranker import RerankerAgent
from config.settings import settings
from utils.llm_factory import get_llm
from cache.exact_cache import exact_cache_manager
from cache.semantic_cache import semantic_cache_manager
from utils.sanitize import strip_reasoning
import asyncio
from utils.logging import workflow_logger as logger
from utils.trace_model import FailureStage, FailureCode

class RetrievalTrace(TypedDict, total=False):
    raw_query: str
    standalone_query: str
    retrieval_queries: List[str]
    dense_results: List[dict]
    sparse_results: List[dict]
    fusion_results: List[dict]
    rerank_results: List[dict]
    selected_results: List[dict]
    latencies: Dict[str, float]

class AgentState(TypedDict):
    question: str
    chat_history: str
    tenant_id: str
    thread_id: str
    attachments: List[dict]
    planner_decision: dict
    current_query: str
    retrieval_queries: List[str]
    documents: List[Document]
    cached_documents: List[Document]
    evidence_conflicts: List[dict]
    draft_answer: str
    verification_report: str
    failure_stage: FailureStage
    first_failure_stage: FailureStage
    failure_code: FailureCode
    failure_reasoning: str
    relevance_result: dict
    retriever: object
    revision_count: int
    retrieval_attempts: int
    feedback: str
    used_cache: bool
    fallback_identical: bool
    rewrite_failed: bool
    turn_status: str
    retrieval_trace: RetrievalTrace
    pipeline_trace: object
    generation_artifacts: object       # GenerationArtifacts: generator_output, evidence_graph, provenance, verification_result
    verification_status: str           # Structured gate: PASS | PASS_WITH_WARNINGS | REPAIRED | FAIL
    response_output: object            # ResponseOutput v1.0 from ResponseComposer (Phase 8)
    certified_response: object         # CertifiedResponse v1.0 from CertificationEngine (Phase 9)

class AgentWorkflow:
    def __init__(self, checkpointer=None):
        self.planner = WorkflowPlanner()
        self.researcher = GeneratorAgent()      # Phase 6: Generation Engine v1.1
        self.verifier = VerificationEngine()    # Phase 7: Verification Engine v1.0
        self.composer = ResponseComposer()      # Phase 8: Response Composer v1.0
        self.certifier = CertificationEngine()  # Phase 9: Certification & Delivery Engine v1.0
        self.relevance_checker = RelevanceChecker()
        self.rewriter = QueryRewriter()
        self.reranker = RerankerAgent()
        
        self.chat_model = get_llm(
            temperature=0.7,
            max_tokens=2048,
            agent_name="chat_responder"
        )
        self.compiled_workflow = self.build_workflow(checkpointer)
        
    def build_workflow(self, checkpointer=None):
        workflow = StateGraph(AgentState)
        
        # Add nodes
        workflow.add_node("plan", self._plan_step)
        workflow.add_node("chat_responder", self._chat_responder_step)
        workflow.add_node("rewrite_query", self._rewrite_query_step)
        workflow.add_node("check_cache", self._check_cache_step)
        workflow.add_node("retrieve", self._retrieve_step)
        workflow.add_node("rerank", self._rerank_step)
        workflow.add_node("check_relevance", self._check_relevance_step)
        workflow.add_node("fallback_rewrite", self._fallback_rewrite_step)
        workflow.add_node("handle_irrelevant", self._handle_irrelevant_step)
        workflow.add_node("evidence_analyzer", self._evidence_analyzer_step) # New Evidence Analyzer Node
        workflow.add_node("research", self._research_step)
        workflow.add_node("verify", self._verification_step)
        workflow.add_node("response_preparation", self._response_preparation_step)  # Phase 8: Response Preparation
        workflow.add_node("output_guardrail", self._output_guardrail_step)
        
        workflow.set_entry_point("plan")
        
        workflow.add_conditional_edges(
            "plan",
            self._route_after_plan,
            {
                "chat": "chat_responder",
                "rewrite_query": "rewrite_query"
            }
        )
        
        workflow.add_edge("chat_responder", "output_guardrail")
        
        workflow.add_conditional_edges(
            "rewrite_query",
            self._route_after_rewrite,
            {
                "check_cache": "check_cache",
                "output_guardrail": "output_guardrail"
            }
        )
        
        workflow.add_conditional_edges(
            "check_cache",
            self._route_after_cache,
            {
                "output_guardrail": "output_guardrail",
                "retrieve": "retrieve",
                "check_relevance": "check_relevance"
            }
        )
        
        workflow.add_edge("retrieve", "rerank")
        workflow.add_edge("rerank", "check_relevance")
        
        workflow.add_conditional_edges(
            "check_relevance",
            self._route_after_relevance,
            {
                "evidence_analyzer": "evidence_analyzer",
                "rewrite_query": "rewrite_query", # Cache miss
                "fallback_rewrite": "fallback_rewrite",
                "handle_irrelevant": "handle_irrelevant"
            }
        )
        
        workflow.add_conditional_edges(
            "fallback_rewrite",
            self._route_after_fallback,
            {
                "retrieve": "retrieve",
                "evidence_analyzer": "evidence_analyzer",
                "handle_irrelevant": "handle_irrelevant"
            }
        )
        workflow.add_edge("handle_irrelevant", "output_guardrail")
        workflow.add_edge("evidence_analyzer", "research") # Route Evidence Analyzer to Research
        workflow.add_edge("research", "verify")

        workflow.add_conditional_edges(
            "verify",
            self._decide_after_verification,
            {
                "re_research": "research",
                "end": "response_preparation"   # Route through Response Preparation before guardrail
            }
        )

        workflow.add_edge("response_preparation", "output_guardrail")
        workflow.add_edge("output_guardrail", END)
        return workflow.compile(checkpointer=checkpointer)

    def _plan_step(self, state: AgentState) -> Dict:
        logger.info("[DEBUG] Entered _plan_step")
        import time
        start_time = time.time()
        attached_ids = [a["id"] for a in state.get("attachments", [])]
        
        decision = self.planner.plan(
            question=state["question"],
            chat_history=state.get("chat_history", ""),
            attached_document_ids=attached_ids
        )
        
        return {
            "planner_decision": decision.model_dump(),
            "retrieval_attempts": 0,
            "revision_count": 0,
            "retrieval_trace": {
                "raw_query": state["question"],
                "latencies": {"plan": time.time() - start_time}
            }
        }

    def _route_after_plan(self, state: AgentState) -> str:
        decision = state.get("planner_decision", {})
        if not decision.get("needs_retrieval", True):
            logger.info("[DEBUG] Routing to chat_responder")
            return "chat_responder"
            
        logger.info("[DEBUG] Needs retrieval -> Routing to rewrite_query.")
        return "rewrite_query"

    def _route_after_rewrite(self, state: AgentState) -> str:
        if state.get("rewrite_failed", False):
            logger.info("[DEBUG] Rewrite failed and needs chat history -> returning graceful failure via output_guardrail.")
            return "output_guardrail"
        return "check_cache"

    def _check_cache_step(self, state: AgentState) -> Dict:
        import time
        import asyncio
        import nest_asyncio
        nest_asyncio.apply()
        start_time = time.time()
        logger.info("[DEBUG] Entered _check_cache_step")
        tenant_id = state.get("tenant_id", "default_tenant")
        thread_id = state.get("thread_id", "default_thread")
        query_to_check = state.get("current_query", state["question"])
        
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
        exact_answer = loop.run_until_complete(exact_cache_manager.check_cache(query_to_check, tenant_id, thread_id))
        if exact_answer:
            logger.info("[DEBUG] Exact Cache Hit in Workflow.")
            return {"draft_answer": exact_answer}
            
        # Get version to pass to synchronous semantic_cache
        version = loop.run_until_complete(exact_cache_manager.get_tenant_version(tenant_id))
        # semantic_cache is synchronous
        semantic_answer = semantic_cache_manager.check_cache(query_to_check, tenant_id, thread_id, version)
        if semantic_answer:
            logger.info("[DEBUG] Semantic Cache Hit in Workflow.")
            return {"draft_answer": semantic_answer}
            
        trace = state.get("retrieval_trace", {})
        if "latencies" not in trace: trace["latencies"] = {}
        trace["latencies"]["check_cache"] = time.time() - start_time
        return {"retrieval_trace": trace}

    def _route_after_cache(self, state: AgentState) -> str:
        if state.get("draft_answer"):
            return "output_guardrail"
            
        # TODO: Document cache routing intentionally disabled pending future implementation.
        # cached_docs = state.get("cached_documents")
        # if cached_docs:
        #     logger.info("[DEBUG] Cached docs found. Routing to check_relevance.")
        #     return "check_relevance"
            
        logger.info("[DEBUG] No cache. Routing to retrieve.")
        return "retrieve"

    def _chat_responder_step(self, state: AgentState) -> Dict:
        logger.info("[DEBUG] Entered _chat_responder_step")
        prompt = f"""
        Answer the user's question directly based on the conversational history.
        You do not need external documents for this.
        
        Chat History:
        {state.get("chat_history", "")}
        
        User Question: {state["question"]}
        """
        response = self.chat_model.invoke(prompt)
        return {"draft_answer": response.content}

    def _rewrite_query_step(self, state: AgentState) -> Dict:
        import time
        start_time = time.time()
        logger.info("[DEBUG] Entered _rewrite_query_step")
        decision = state.get("planner_decision", {})
        trace = state.get("retrieval_trace", {})
        if "latencies" not in trace: trace["latencies"] = {}
        
        if not decision.get("needs_query_expansion", True) and not decision.get("needs_chat_history", False):
            trace["standalone_query"] = state["question"]
            trace["retrieval_queries"] = [state["question"]]
            trace["latencies"]["rewrite_query"] = time.time() - start_time
            return {
                "current_query": state["question"],
                "retrieval_queries": [state["question"]],
                "retrieval_trace": trace
            }
            
        new_query_data = self.rewriter.rewrite(
            original_question=state["question"],
            chat_history=state.get("chat_history", ""),
            needs_chat_history=decision.get("needs_chat_history", False)
        )
        
        trace["standalone_query"] = new_query_data["standalone_query"]
        trace["retrieval_queries"] = new_query_data["retrieval_queries"]
        trace["latencies"]["rewrite_query"] = time.time() - start_time
        
        result_state = {
            "current_query": new_query_data["standalone_query"],
            "retrieval_queries": new_query_data["retrieval_queries"],
            "retrieval_trace": trace
        }
        
        if new_query_data.get("rewrite_failed", False):
            result_state["rewrite_failed"] = True
            result_state["draft_answer"] = "I couldn't resolve your follow-up question. Please mention the topic again."
            result_state["failure_stage"] = FailureStage.REWRITE
            result_state["failure_code"] = FailureCode.REWRITE_FAILED
            result_state["failure_reasoning"] = "Query rewriting failed"
            if state.get("first_failure_stage", FailureStage.NONE) == FailureStage.NONE:
                result_state["first_failure_stage"] = FailureStage.REWRITE
            
        return result_state

    def _retrieve_step(self, state: AgentState) -> Dict:
        import time
        start_time = time.time()
        logger.info(f"[DEBUG] Entered _retrieve_step")
        queries = state.get("retrieval_queries", [state.get("current_query", state["question"])])
        retriever = state["retriever"]
        
        # Inject document IDs to search from planner
        decision = state.get("planner_decision", {})
        docs_to_search = decision.get("document_ids_to_search", [])
        if hasattr(retriever, 'document_ids'):
            retriever.document_ids = docs_to_search
            
        # Inject intent type for intent-based routing
        if hasattr(retriever, 'intent_type'):
            retriever.intent_type = decision.get("intent_type")
            
        # Inject target date for historical range query filtering
        if hasattr(retriever, 'target_date'):
            retriever.target_date = decision.get("target_date")
            
        # Inject domains
        domains = decision.get("domains", [])
        if domains and hasattr(retriever, 'domain'):
            retriever.domain = domains[0] if isinstance(domains, list) and domains else None

        # Check for fallback search (attempts > 0) -> Global Search Fallback
        if state.get("retrieval_attempts", 0) > 0:
            logger.info("[Global Search Fallback] Fallback attempt > 0. Clearing scope constraints.")
            if hasattr(retriever, 'document_ids'):
                retriever.document_ids = None
            if hasattr(retriever, 'domain'):
                retriever.domain = None
            
        all_documents = []
        seen_ids = set()
        
        trace = state.get("retrieval_trace", {})
        
        for q in queries:
            logger.info(f"Retrieving for sub-query: {q}")
            documents = retriever.invoke(q)
            
            # Extract trace from retriever if it's our hybrid retriever
            if hasattr(retriever, "last_trace"):
                for k, v in retriever.last_trace.items():
                    if isinstance(v, list):
                        if k not in trace or not isinstance(trace[k], list):
                            trace[k] = []
                        trace[k].extend(v)
                    elif isinstance(v, dict):
                        if k not in trace or not isinstance(trace[k], dict):
                            trace[k] = {}
                        trace[k].update(v)
                    else:
                        trace[k] = v
            
            for doc in documents:
                # Add provenance metadata
                if "retrieved_from_queries" not in doc.metadata:
                    doc.metadata["retrieved_from_queries"] = []
                if q not in doc.metadata["retrieved_from_queries"]:
                    doc.metadata["retrieved_from_queries"].append(q)
                
                doc_id = doc.metadata.get("id", doc.page_content[:50])
                if doc_id not in seen_ids:
                    seen_ids.add(doc_id)
                    all_documents.append(doc)
                    
        logger.info(f"Primary retrieval fetched total {len(all_documents)} unique chunks.")
        
        # Graph Expansion (Hop 2)
        # Query PG for confirmed relationships with Attenuated Path Decay
        expanded_docs = []
        from db.database import SessionLocal
        db = SessionLocal()
        try:
            expanded_docs = self._expand_document_graph(db, all_documents, state.get("tenant_id", "default_tenant"))
        except Exception as e:
            logger.error(f"Error during graph expansion: {e}")
        finally:
            db.close()
            
        # Append the expanded documents
        for doc in expanded_docs:
            doc_id = doc.metadata.get("id", doc.page_content[:50])
            if doc_id not in seen_ids:
                seen_ids.add(doc_id)
                all_documents.append(doc)
                
        logger.info(f"Retrieved total {len(all_documents)} unique chunks (including graph expansion).")
        for i, doc in enumerate(all_documents[:3]): # Log top 3 for debugging
            doc_source = doc.metadata.get('source') or doc.metadata.get('file_name') or doc.metadata.get('s3_key') or doc.metadata.get('title') or 'Unknown'
            logger.info(f"  [Chunk {i+1}] Source: {doc_source} | Queries: {doc.metadata.get('retrieved_from_queries')} | Text: {doc.page_content[:150]}...")

            
        if "latencies" not in trace: trace["latencies"] = {}
        trace["latencies"]["retrieve"] = time.time() - start_time
        
        failure_stage = FailureStage.NONE
        failure_code = FailureCode.NONE
        failure_reasoning = ""
        if len(all_documents) == 0:
            failure_stage = FailureStage.RETRIEVAL
            failure_code = FailureCode.NO_DOCUMENTS
            failure_reasoning = "Retrieval returned 0 documents."
            
        first_failure = state.get("first_failure_stage", FailureStage.NONE)
        if failure_stage != FailureStage.NONE and first_failure == FailureStage.NONE:
            first_failure = failure_stage
            
        return {
            "documents": all_documents, 
            "used_cache": False, 
            "retrieval_trace": trace,
            "failure_stage": failure_stage if failure_stage != FailureStage.NONE else state.get("failure_stage", FailureStage.NONE),
            "failure_code": failure_code if failure_code != FailureCode.NONE else state.get("failure_code", FailureCode.NONE),
            "failure_reasoning": failure_reasoning if failure_reasoning else state.get("failure_reasoning", ""),
            "first_failure_stage": first_failure
        }

    def _expand_document_graph(self, db, base_docs: List[Document], tenant_id: str) -> List[Document]:
        from db.models import Document as SQLDocument, DocumentRelationship
        import math
        
        expanded_docs = []
        seen_doc_ids = {doc.metadata.get("id") for doc in base_docs if doc.metadata.get("id")}
        
        # Load decay settings from settings with fallback defaults
        decay_factor = getattr(settings, "RELATIONSHIP_DECAY_FACTOR", 0.1)
        conf_human = getattr(settings, "RELATIONSHIP_CONFIDENCE_HUMAN", 1.0)
        conf_regex = getattr(settings, "RELATIONSHIP_CONFIDENCE_REGEX", 0.99)
        conf_llm = getattr(settings, "RELATIONSHIP_CONFIDENCE_LLM", 0.80)
        min_conf = getattr(settings, "MIN_RELATION_CONFIDENCE", 0.60)
        
        # Identify source documents
        source_doc_ids = set()
        for doc in base_docs:
            sql_doc_id = doc.metadata.get("document_id")
            if sql_doc_id:
                source_doc_ids.add(sql_doc_id)
                
        for s_id in source_doc_ids:
            # Query relationships in both directions
            relations = db.query(DocumentRelationship).filter(
                (DocumentRelationship.source_document_id == s_id) | 
                (DocumentRelationship.target_document_id == s_id)
            ).all()
            
            for rel in relations:
                target_id = rel.target_document_id if rel.source_document_id == s_id else rel.source_document_id
                
                # Link confidence calculation based on extracted_by
                if rel.extracted_by == "admin":
                    link_conf = conf_human
                elif rel.extracted_by == "regex":
                    link_conf = conf_regex
                elif rel.extracted_by == "LLM":
                    link_conf = conf_llm
                else:
                    link_conf = rel.extraction_confidence or 0.80
                    
                # Attenuated Path Decay Formula: Link Confidence * e^(-lambda * hop_count)
                path_conf = link_conf * math.exp(-decay_factor * 1)
                
                if path_conf < min_conf:
                    logger.debug(f"[Graph Expansion] Pruning relation {rel.relation_type} to doc {target_id} (confidence {path_conf:.2f} < threshold {min_conf})")
                    continue
                    
                # Get target document details for logging / provenance
                sql_doc = db.query(SQLDocument).filter(SQLDocument.id == target_id).first()
                if not sql_doc:
                    continue
                
                family_title = sql_doc.document_family.title if sql_doc.document_family else "Related Law"
                
                # Query Qdrant for this target document's chunks
                from qdrant_client.models import Filter, FieldCondition, MatchValue
                from db.qdrant_client import qdrant_manager
                
                related_filter = Filter(must=[
                    FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
                    FieldCondition(key="document_id", match=MatchValue(value=target_id))
                ])
                
                try:
                    logger.info(f"[Graph Expansion] Expanding to related doc '{family_title}' (id={target_id}) via {rel.relation_type} (conf={path_conf:.2f})")
                    records, _ = qdrant_manager.client.scroll(
                        collection_name=qdrant_manager.collection_name,
                        scroll_filter=related_filter,
                        limit=10
                    )
                    
                    for rec in records:
                        hit_meta = rec.payload or {}
                        document = hit_meta.get("document", "")
                        metadata = {k: v for k, v in hit_meta.items() if k != "document"}
                        hit_id = metadata.get("id", document[:50])
                        if hit_id not in seen_doc_ids:
                            seen_doc_ids.add(hit_id)
                            # Tag metadata with graph expansion details
                            metadata["vector_score"] = path_conf
                            metadata["graph_expansion"] = True
                            metadata["relation_type"] = rel.relation_type
                            metadata["relation_source"] = family_title
                            
                            expanded_docs.append(Document(page_content=document, metadata=metadata))
                except Exception as ex:
                    logger.error(f"[Graph Expansion] Failed to retrieve chunks for related doc {target_id}: {ex}")
                    
        return expanded_docs

    def _rerank_step(self, state: AgentState) -> Dict:
        import time
        start_time = time.time()
        query = state.get("current_query", state["question"])
        has_cit = state.get("has_citation", False) or bool(state.get("article_keys"))
        exact_key = state.get("article_keys")[0] if state.get("article_keys") else None
        reranked = self.reranker.rerank(query, state["documents"], has_citation=has_cit, exact_citation_key=exact_key)
        
        logger.info(f"Reranked {len(reranked)} chunks.")
        for i, doc in enumerate(reranked[:3]): # Log top 3 reranked
            rerank_score = doc.metadata.get('rerank_score', doc.metadata.get('vector_score', 0.0))
            doc_source = doc.metadata.get('source') or doc.metadata.get('file_name') or doc.metadata.get('s3_key') or doc.metadata.get('title') or 'Unknown'
            logger.info(f"  [LLM Rank {i+1}] Rerank Score: {rerank_score} | Source: {doc_source} | Text: {doc.page_content[:150]}...")

            
        trace = state.get("retrieval_trace", {})
        if "latencies" not in trace: trace["latencies"] = {}
        trace["latencies"]["rerank"] = time.time() - start_time
        
        failure_stage = FailureStage.NONE
        failure_code = FailureCode.NONE
        failure_reasoning = ""
        
        if len(reranked) == 0 and len(state.get("documents", [])) > 0:
            failure_stage = FailureStage.RERANKING
            failure_code = FailureCode.BELOW_THRESHOLD
            failure_reasoning = "Reranker dropped all retrieved documents below threshold."
            
        first_failure = state.get("first_failure_stage", FailureStage.NONE)
        if failure_stage != FailureStage.NONE and first_failure == FailureStage.NONE:
            first_failure = failure_stage
            
        # Update cache with the new reranked documents
        return {
            "documents": reranked, 
            "cached_documents": reranked, 
            "retrieval_trace": trace,
            "failure_stage": failure_stage if failure_stage != FailureStage.NONE else state.get("failure_stage", FailureStage.NONE),
            "failure_code": failure_code if failure_code != FailureCode.NONE else state.get("failure_code", FailureCode.NONE),
            "failure_reasoning": failure_reasoning if failure_reasoning else state.get("failure_reasoning", ""),
            "first_failure_stage": first_failure
        }

    def _check_relevance_step(self, state: AgentState) -> Dict:
        import time
        start_time = time.time()
        logger.info(f"[DEBUG] Entered _check_relevance_step")
        docs_to_check = state.get("cached_documents") if state.get("used_cache", True) else state.get("documents", [])
        
        has_cit = state.get("has_citation", False) or bool(state.get("article_keys"))
        exact_key = state.get("article_keys")[0] if state.get("article_keys") else None

        result = self.relevance_checker.check(
            query=state.get("current_query", state["question"]), 
            documents=docs_to_check,
            raw_question=state["question"],
            planner_decision=state.get("planner_decision", {}),
            has_citation=has_cit,
            exact_citation_key=exact_key
        )
        
        logger.info(f"Relevance Result: {result.model_dump_json(indent=2)}")
        
        trace = state.get("retrieval_trace", {})
        if "latencies" not in trace: trace["latencies"] = {}
        trace["latencies"]["check_relevance"] = time.time() - start_time
        
        failure_stage = FailureStage.NONE if result.should_generate else FailureStage.RELEVANCE
        failure_code = FailureCode.NONE if result.should_generate else FailureCode.INSUFFICIENT_EVIDENCE
        first_failure = state.get("first_failure_stage", FailureStage.NONE)
        if failure_stage != FailureStage.NONE and first_failure == FailureStage.NONE:
            first_failure = failure_stage

        relevance_trace = getattr(self.relevance_checker, "last_trace", {})

        return {
            "relevance_result": result.model_dump(),
            "relevance_trace": relevance_trace,
            "generation_hints": result.generation_hints,
            "failure_stage": failure_stage,
            "failure_code": failure_code,
            "failure_reasoning": result.reasoning,
            "first_failure_stage": first_failure,
            "documents": docs_to_check,
            "retrieval_trace": trace
        }

    def _route_after_relevance(self, state: AgentState) -> str:
        relevance_result = state.get("relevance_result", {})
        should_gen = relevance_result.get("should_generate", False)
        attempts = state.get("retrieval_attempts", 0)
        max_retries = getattr(settings, "MAX_RETRIEVAL_RETRIES", 2)
        
        if should_gen:
            logger.info("[DEBUG] Relevance decision should_generate=True -> evidence_analyzer")
            return "evidence_analyzer"
            
        if attempts < max_retries:
            logger.info(f"[DEBUG] Insufficient evidence (attempt {attempts}/{max_retries}) -> fallback_rewrite")
            return "fallback_rewrite"
            
        planner = state.get("planner_decision", {})
        query_type = planner.get("query_type", "single_entity")
        complex_query = query_type in ["comparison", "sequence", "multi_hop", "follow_up"]
        
        if complex_query and relevance_result.get("weighted_coverage_score", 0.0) >= 0.50:
            logger.info("[DEBUG] Max retries reached, but weighted coverage >= 0.50. Routing to evidence_analyzer.")
            return "evidence_analyzer"
                
        logger.info("[DEBUG] Maximum retrieval retry count reached (Loop Escape Guardrail) -> handle_irrelevant")
        return "handle_irrelevant"
        
        if complex_query and "entity_coverage" in relevance_result:
            if any(score > 0.5 for score in relevance_result["entity_coverage"].values()):
                logger.info("[DEBUG] Out of attempts, but complex query has decent entity coverage. Routing to evidence_analyzer.")
                return "evidence_analyzer"
                
        logger.info("[DEBUG] No match or out of attempts -> handle_irrelevant")
        return "handle_irrelevant"

    def _fallback_rewrite_step(self, state: AgentState) -> Dict:
        import time
        start_time = time.time()
        logger.info("[DEBUG] Entered _fallback_rewrite_step")
        original_query = state.get("current_query", state["question"])
        old_queries = state.get("retrieval_queries", [])
        
        new_query_data = self.rewriter.rewrite(
            original_question=original_query, 
            chat_history=state.get("chat_history", ""),
            failure_reason=state.get("failure_reasoning", "Insufficient context")
        )
        
        new_queries = new_query_data["retrieval_queries"]
        fallback_identical = sorted(new_queries) == sorted(old_queries)
        if fallback_identical:
            logger.info("[DEBUG] Fallback generated identical queries. Short-circuiting redundant retrieval.")

        trace = state.get("retrieval_trace", {})
        if "latencies" not in trace: trace["latencies"] = {}
        trace["latencies"]["fallback_rewrite"] = time.time() - start_time

        return {
            "current_query": new_query_data["standalone_query"],
            "retrieval_queries": new_queries,
            "retrieval_attempts": state.get("retrieval_attempts", 0) + 1,
            "fallback_identical": fallback_identical,
            "retrieval_trace": trace
        }

    def _route_after_fallback(self, state: AgentState) -> str:
        if state.get("fallback_identical"):
            docs = state.get("documents", [])
            has_docs = len(docs) > 0
            
            planner = state.get("planner_decision", {})
            query_type = planner.get("query_type", "single_entity")
            complex_query = query_type in ["comparison", "sequence", "multi_hop", "follow_up"]
            
            relevance_result = state.get("relevance_result", {})
            decent_coverage = False
            if "entity_coverage" in relevance_result:
                decent_coverage = any(score > 0.5 for score in relevance_result["entity_coverage"].values())
            
            if has_docs and decent_coverage and complex_query:
                logger.info("[DEBUG] Identical fallback, but complex query has decent entity coverage. Routing to evidence_analyzer.")
                return "evidence_analyzer"
            else:
                logger.info("[DEBUG] Identical fallback and insufficient chunks. Routing to handle_irrelevant.")
                return "handle_irrelevant"
                
        return "retrieve"

    def _handle_irrelevant_step(self, state: AgentState) -> Dict:
        logger.info("[DEBUG] Entered _handle_irrelevant")
        msg = AIMessage(content="I couldn't find enough evidence in the uploaded documents to answer this accurately.")
        return {"draft_answer": msg.content, "messages": [msg], "documents": state.get("documents", []), "cached_documents": state.get("cached_documents"), "turn_status": "no_answer"}

    def _evidence_analyzer_step(self, state: AgentState) -> Dict:
        logger.info("[DEBUG] Entered _evidence_analyzer_step")
        documents = state.get("documents", [])
        tenant_id = state.get("tenant_id", "default_tenant")
        
        from db.database import SessionLocal
        from db.models import Document as SQLDocument, AuthorityRank
        
        db = SessionLocal()
        conflicts = []
        updated_documents = []
        
        try:
            # 1. Fetch all Authority Ranks for dynamic lookup
            ranks = db.query(AuthorityRank).all()
            rank_map = {r.document_type.lower(): r.rank for r in ranks}
            
            # Default fallback hierarchy if table is empty
            default_ranks = {
                "federal law": 100,
                "قانون اتحادي": 100,
                "cabinet resolution": 80,
                "cabinet decision": 80,
                "قرار مجلس الوزراء": 80,
                "executive regulation": 80,
                "اللائحة التنفيذية": 80,
                "ministerial resolution": 60,
                "ministerial decision": 60,
                "قرار وزاري": 60,
                "local policy": 40,
                "policy": 40,
                "سياسة": 40
            }
            
            def get_auth_rank(doc_title, doc_type):
                dtype = (doc_type or "").lower().strip()
                title_lower = (doc_title or "").lower()
                
                # Check DB rank map first
                if dtype in rank_map:
                    return rank_map[dtype]
                
                # Check string matching in title or type
                for key, val in rank_map.items():
                    if key in dtype or key in title_lower:
                        return val
                        
                # Check defaults
                for key, val in default_ranks.items():
                    if key in dtype or key in title_lower:
                        return val
                return 50 # middle fallback
                
            # 2. Extract database metadata for each retrieved chunk
            doc_metadata_cache = {}
            for doc in documents:
                doc_id = doc.metadata.get("document_id")
                if not doc_id:
                    updated_documents.append(doc)
                    continue
                    
                if doc_id not in doc_metadata_cache:
                    sql_doc = db.query(SQLDocument).filter(SQLDocument.id == doc_id).first()
                    if sql_doc:
                        family_title = sql_doc.document_family.title if sql_doc.document_family else "Unknown Law"
                        domain = sql_doc.document_family.domain if sql_doc.document_family else "Legal"
                        rank_val = get_auth_rank(family_title, domain)
                        
                        doc_metadata_cache[doc_id] = {
                            "title": family_title,
                            "version": sql_doc.version,
                            "lifecycle_status": sql_doc.lifecycle_status,
                            "authority_rank": rank_val,
                            "effective_date": str(sql_doc.effective_date_gregorian or "Unknown")
                        }
                    else:
                        doc_metadata_cache[doc_id] = None
                        
                meta = doc_metadata_cache[doc_id]
                if meta:
                    doc.metadata["authority_rank"] = meta["authority_rank"]
                    doc.metadata["version"] = meta["version"]
                    doc.metadata["lifecycle_status"] = meta["lifecycle_status"]
                    doc.metadata["title"] = meta["title"]
                    doc.metadata["effective_date"] = meta["effective_date"]
                updated_documents.append(doc)
                
            # 3. Analyze Conflicts:
            family_groups = {}
            for doc in updated_documents:
                title = doc.metadata.get("title")
                if title:
                    if title not in family_groups:
                        family_groups[title] = []
                    family_groups[title].append(doc)
                    
            for title, group in family_groups.items():
                versions = {d.metadata.get("version") for d in group if d.metadata.get("version")}
                if len(versions) > 1:
                    statuses = {d.metadata.get("lifecycle_status") for d in group if d.metadata.get("lifecycle_status")}
                    conflict_desc = f"Multiple versions of '{title}' found: {list(versions)} (Statuses: {list(statuses)})"
                    logger.warning(f"[Evidence Analyzer] Version Conflict: {conflict_desc}")
                    conflicts.append({
                        "type": "VERSION_CONFLICT",
                        "description": conflict_desc,
                        "family": title,
                        "versions": list(versions)
                    })
                    
                    for doc in group:
                        doc.metadata["version_conflict"] = True
                        if doc.metadata.get("lifecycle_status") == "Superseded":
                            doc.metadata["authority_warning"] = "WARNING: This text is from a SUPERSEDED version of the law."
                            
            unique_sources = list(doc_metadata_cache.values())
            unique_sources = [s for s in unique_sources if s]
            if len(unique_sources) > 1:
                highest_rank_doc = max(unique_sources, key=lambda x: x["authority_rank"])
                for doc in updated_documents:
                    rank = doc.metadata.get("authority_rank", 50)
                    if rank < highest_rank_doc["authority_rank"]:
                        doc.metadata["hierarchy_note"] = f"Note: This source has lower authority level than '{highest_rank_doc['title']}'."
                        
        except Exception as e:
            logger.error(f"Error during evidence analysis: {e}")
        finally:
            db.close()
            
        return {
            "documents": updated_documents,
            "evidence_conflicts": conflicts
        }

    def _research_step(self, state: AgentState) -> Dict:
        import time
        start_time = time.time()
        logger.info(f"[DEBUG] Entered _research_step")

        query_to_answer = state.get("current_query", state["question"])
        documents = state.get("documents", [])

        # ── Modification 1: Build EvidenceReasoningGraph ONCE here ──────────────────
        # This is the single canonical graph. Verifier reads from state — never rebuilds.
        # Rebuilding in two places would risk Graph_A (Generator) ≠ Graph_B (Verifier).
        evidence_graph = EvidenceReasoningGraphBuilder.build_graph(documents)

        relevance_decision = state.get("relevance_result", {})
        planner_decision = state.get("planner_decision", {})

        logger.info(f"[Generator] Calling GeneratorAgent v1.1 with {len(documents)} documents.")
        gen_out = self.researcher.generate(
            query=query_to_answer,
            documents=documents,
            relevance_decision=relevance_decision,
            planner_decision=planner_decision
        )

        # ── Modification 4: Package GenerationArtifacts ─────────────────────────────
        # One structured container consumed by downstream nodes (Verifier, ResponsePrep).
        generation_artifacts = {
            "generator_output": gen_out,
            "evidence_reasoning_graph": evidence_graph,
            "prompt_metadata": gen_out.draft_metadata.model_dump() if getattr(gen_out, "draft_metadata", None) else {},
            "provenance": gen_out.provenance.model_dump() if getattr(gen_out, "provenance", None) else {},
            "verification_result": None,
            "verification_report_obj": None,
        }

        trace = state.get("retrieval_trace", {})
        if "latencies" not in trace:
            trace["latencies"] = {}
        trace["latencies"]["research"] = time.time() - start_time
        trace["prompt_version"] = getattr(gen_out, "generator_schema_version", "1.1")

        return {
            "draft_answer": gen_out.structured_answer,
            "generation_artifacts": generation_artifacts,
            "retrieval_trace": trace
        }
    
    def _verification_step(self, state: AgentState) -> Dict:
        import time
        start_time = time.time()
        logger.info("[DEBUG] Entered _verification_step")

        # ── Modification 1: Consume GenerationArtifacts from state (never rebuild graph) ──
        artifacts = dict(state.get("generation_artifacts") or {})
        gen_out = artifacts.get("generator_output")
        evidence_graph = artifacts.get("evidence_reasoning_graph")

        trace = state.get("retrieval_trace", {})
        if "latencies" not in trace:
            trace["latencies"] = {}

        # Guard: if artifacts missing (cache hit path), pass through cleanly
        if gen_out is None or evidence_graph is None:
            logger.warning("[Verifier] generation_artifacts missing — pass-through (cache hit path).")
            trace["latencies"]["verification"] = time.time() - start_time
            return {
                "verification_report": "**Supported:** YES\n**Relevant:** YES\n**Failure Reason:** NONE",
                "verification_status": "PASS",
                "failure_stage": FailureStage.NONE,
                "failure_code": FailureCode.NONE,
                "failure_reasoning": "NONE",
                "first_failure_stage": state.get("first_failure_stage", FailureStage.NONE),
                "feedback": "",
                "revision_count": state.get("revision_count", 0) + 1,
                "retrieval_trace": trace
            }

        # ── Modification 3: GeneratorOutput is IMMUTABLE ────────────────────────────
        # Build VerificationInput — generator_output is passed by reference, never modified.
        # Any repairs live in VerificationResult.repaired_answer only.
        inp = VerificationInput(
            generator_output=gen_out,
            evidence_reasoning_graph=evidence_graph,
            raw_query=state.get("current_query", state["question"]),
            relevance_decision=state.get("relevance_result", {})
        )

        result, report_obj = self.verifier.verify(inp)

        # Use repaired_answer if repair was applied. GeneratorOutput stays immutable.
        final_answer = result.repaired_answer if result.repaired_answer else state.get("draft_answer", "")

        # Append verification artifacts to container (do NOT mutate generator_output)
        artifacts["verification_result"] = result
        artifacts["verification_report_obj"] = report_obj

        # ── Modification 2: Structured verification_status + backward-compatible string ──
        verification_report_str = self._serialize_verification_result(result)
        failure_reason = self._map_failure_reason(result)
        failure_stage = FailureStage.VERIFICATION if result.overall_status == "FAIL" else FailureStage.NONE
        failure_code = FailureCode.UNSUPPORTED if result.overall_status == "FAIL" else FailureCode.NONE

        first_failure = state.get("first_failure_stage", FailureStage.NONE)
        if failure_stage != FailureStage.NONE and first_failure == FailureStage.NONE:
            first_failure = failure_stage

        trace["latencies"]["verification"] = time.time() - start_time

        return {
            "draft_answer": final_answer,
            "verification_report": verification_report_str,
            "verification_status": result.overall_status,   # Structured field — preferred by downstream
            "generation_artifacts": artifacts,
            "failure_stage": failure_stage,
            "failure_code": failure_code,
            "failure_reasoning": failure_reason,
            "first_failure_stage": first_failure,
            "feedback": verification_report_str,
            "revision_count": state.get("revision_count", 0) + 1,
            "retrieval_trace": trace
        }

    
    def _decide_after_verification(self, state: AgentState) -> str:
        revisions = state.get("revision_count", 0)
        reason = state.get("failure_reasoning", "NONE")

        # ── Modification 2: Prefer structured verification_status (phase-in migration) ──
        # Once all deployments are stable, remove the string-parsing fallback below.
        verification_status = state.get("verification_status", "")
        if verification_status:
            if verification_status in ("PASS", "PASS_WITH_WARNINGS", "REPAIRED"):
                return "end"
            if revisions >= 2:
                return "end"
            if reason == "WRONG_REASONING":
                return "re_research"
            return "end"

        # Fallback: legacy string-pattern parsing (temporary backward-compatibility layer)
        report = state.get("verification_report", "")
        if ("Supported: NO" not in report and "Relevant: NO" not in report
                and "Supported:** NO" not in report and "Relevant:** NO" not in report):
            return "end"
        if revisions >= 2:
            return "end"
        if reason == "WRONG_REASONING":
            return "re_research"
        return "end"

    def _output_guardrail_step(self, state: AgentState) -> Dict:
        import time
        import os
        import json
        start_time = time.time()
        logger.info("[DEBUG] Entered _output_guardrail_step")
        
        report = state.get("verification_report", "")
        reason = state.get("failure_reasoning", "NONE")
        revisions = state.get("revision_count", 0)
        
        sanitized_answer = state["draft_answer"]
        turn_status = state.get("turn_status", "success")
        documents = state.get("documents", [])
        
        # Handle Verification Failures — prefer structured status, fallback to string parsing
        verification_status_val = state.get("verification_status", "")
        is_verification_failed = (
            verification_status_val == "FAIL" or
            "Supported: NO" in report or "Relevant: NO" in report or
            "Supported:** NO" in report or "Relevant:** NO" in report
        )
        if is_verification_failed:
            if reason in ["MISSING_EVIDENCE", "NO_ANSWER_IN_DOC"]:
                logger.warning(f"[DEBUG] Verification failed due to {reason}. Overriding answer and clearing docs.")
                sanitized_answer = "I couldn't find enough evidence in the uploaded documents to answer this accurately."
                turn_status = "no_answer"
                documents = [] # Clear citations
            elif revisions >= 2:
                logger.warning("[DEBUG] Verification failed 2 times. Prepending warning.")
                sanitized_answer = "I couldn't verify the reasoning internally. Here is my best attempt: " + sanitized_answer
                turn_status = "no_answer"
        
        try:
            from security.guardrails.pii_guardrail import pii_guardrail
            sanitized_answer = pii_guardrail.sanitize(sanitized_answer)
        except Exception as e:
            logger.error(f"Failed to run output guardrail: {e}")
            
        sanitized_answer = strip_reasoning(sanitized_answer)
            
        trace = state.get("retrieval_trace", {})
        if "latencies" not in trace: trace["latencies"] = {}
        trace["latencies"]["output_guardrail"] = time.time() - start_time

        # Cache saving is deferred to main.py to allow streaming to start instantly

        state_updates = {
            "draft_answer": sanitized_answer, 
            "turn_status": turn_status,
            "documents": documents,
            "retrieval_trace": trace
        }
        if turn_status == "no_answer" and reason in ["MISSING_EVIDENCE", "NO_ANSWER_IN_DOC"]:
             state_updates["cached_documents"] = []
             state_updates["verification_report"] = f"Verification Failed: {reason}"
             
        return state_updates

    # ─────────────────────────────────────────────────────────────────────────────
    # Serialization & Mapping Helpers (Verification Engine v1.0 integration)
    # ─────────────────────────────────────────────────────────────────────────────

    def _serialize_verification_result(self, result) -> str:
        """
        Serializes VerificationResult v1.0 to a backward-compatible report string.
        Preserves the exact patterns expected by legacy consumers:
          - _decide_after_verification (string fallback path)
          - _output_guardrail_step (string fallback path)
          - api/main.py log_query_evolution()
        """
        supported = "NO" if result.overall_status == "FAIL" else "YES"
        relevant = "NO" if result.overall_status == "FAIL" else "YES"
        unsupported = [c.statement[:80] for c in (result.unsupported_claims or [])]
        unsupported_str = ", ".join(unsupported) if unsupported else "None"
        contradictions_str = "; ".join((result.consistency_errors or [])[:2]) or "None"
        failure_reason = self._map_failure_reason(result)
        return (
            f"**Supported:** {supported}\n"
            f"**Unsupported Claims:** {unsupported_str}\n"
            f"**Contradictions:** {contradictions_str}\n"
            f"**Relevant:** {relevant}\n"
            f"**Additional Details:** Score={result.scores.overall_score:.3f} "
            f"| Status={result.overall_status} | Repair={result.provenance.repair_mode}\n"
            f"**Failure Reason:** {failure_reason}"
        )

    def _map_failure_reason(self, result) -> str:
        """Maps VerificationFailureMode to legacy failure reason strings for backward compatibility."""
        if result.overall_status != "FAIL":
            return "NONE"
        fm = result.failure_mode
        if fm in ("UNSUPPORTED_CLAIM", "ORPHAN_CITATION", "INVALID_CITATION", "MISSING_DISCLAIMER", "MISSING_WARNING"):
            return "MISSING_EVIDENCE"
        if fm in ("PIPELINE_CONTRACT_VIOLATION", "SUPERSESSION_ERROR", "CONTRADICTED_CLAIM"):
            return "WRONG_REASONING"
        if fm in ("INVALID_SCHEMA", "REPAIR_FAILED"):
            return "NO_ANSWER_IN_DOC"
        return "NONE"

    # ─────────────────────────────────────────────────────────────────────────────────
    # Phase 8 — Response Preparation Node
    # Calls ResponseComposer.compose() which runs all 7 sub-engines (E0–E6).
    # Produces ResponseOutput v1.0 — the only object the API and UI consume.
    # GeneratorOutput, VerificationResult, GenerationArtifacts remain internal-only.
    # ─────────────────────────────────────────────────────────────────────────────────

    def _response_preparation_step(self, state: AgentState) -> Dict:
        logger.info("[Composer] _response_preparation_step: enter")

        generation_artifacts = state.get("generation_artifacts")
        retrieval_trace      = state.get("retrieval_trace") or {}
        pipeline_trace       = state.get("pipeline_trace")
        thread_id            = state.get("thread_id", "default")

        # Derive trace_id from pipeline_trace if available
        trace_id = (
            pipeline_trace.trace_id
            if pipeline_trace and hasattr(pipeline_trace, "trace_id")
            else f"TRACE_{thread_id}"
        )

        # Map query_type -> PresentationProfile
        planner_decision = state.get("planner_decision") or {}
        query_type = planner_decision.get("query_type", "single_entity")
        profile_map = {
            "comparison":   "COMPARISON",
            "sequence":     "PROCEDURAL",
            "multi_hop":    "LEGAL_DETAILED",
            "follow_up":    "LEGAL_STANDARD",
        }
        profile = profile_map.get(query_type, "LEGAL_STANDARD")

        # Detect cache hit
        cache_hit = bool(state.get("used_cache", False)) and (generation_artifacts is None)
        if cache_hit:
            generation_artifacts = {"cached_answer": state.get("draft_answer", "")}

        # Compose ResponseOutput v1.0
        response_output: ResponseOutput = self.composer.compose(
            generation_artifacts=generation_artifacts,
            retrieval_trace=retrieval_trace if isinstance(retrieval_trace, dict) else {},
            pipeline_trace_id=trace_id,
            profile=profile,
            channel="API",
            cache_hit=cache_hit,
        )

        logger.info(
            f"[Composer] ResponseOutput assembled "
            f"| status={response_output.status} "
            f"| citations={len(response_output.citations)} "
            f"| warnings={len(response_output.warnings)} "
            f"| confidence={response_output.confidence:.3f}"
        )

        return {
            "draft_answer":    response_output.answer,
            "response_output": response_output,
        }

    # ─────────────────────────────────────────────────────────────────────────────────
    # Phase 9 — Certification & Delivery Node
    # Calls CertificationEngine.certify() which runs all 6 sub-engines (E1–E6).
    # Produces immutable CertifiedResponse v1.0 with cryptographic SHA256 audit hash.
    # Fail-Closed: 0 output text/citation mutation.
    # ─────────────────────────────────────────────────────────────────────────────────

    def _certification_delivery_step(self, state: AgentState) -> Dict:
        logger.info("[Certifier] _certification_delivery_step: enter")

        response_output = state.get("response_output")
        if not response_output:
            logger.warning("[Certifier] No response_output in state; returning empty")
            return {}

        certified: CertifiedResponse = self.certifier.certify(response_output)

        logger.info(
            f"[Certifier] CertifiedResponse issued "
            f"| status={certified.certification_status} "
            f"| checksum={certified.checksum[:10]}... "
            f"| issues={len(certified.issues)}"
        )

        return {
            "draft_answer":       certified.response_output.answer,
            "certified_response": certified,
        }

    def full_pipeline(self, question: str, retriever: object, chat_history: str = "", thread_id: str = "default_thread", tenant_id: str = "default_tenant", attachments: List[dict] = None):
        try:
            logger.info(f"[DEBUG] Starting pipeline on thread_id='{thread_id}'")
            initial_state = {
                "question": question,
                "chat_history": chat_history,
                "tenant_id": tenant_id,
                "thread_id": thread_id,
                "attachments": attachments or [],
                "current_query": question,
                "retrieval_queries": [question],
                "draft_answer": "",
                "verification_report": "",
                "verification_status": "",
                "generation_artifacts": None,
                "response_output": None,
                "certified_response": None,
                "failure_stage": FailureStage.NONE,
                "first_failure_stage": FailureStage.NONE,
                "failure_code": FailureCode.NONE,
                "failure_reasoning": "",
                "relevance_result": {},
                "retriever": retriever,
                "revision_count": 0,
                "retrieval_attempts": 0,
                "feedback": "",
                "used_cache": True,
                "fallback_identical": False,
                "turn_status": "success"
            }
            
            final_state = initial_state
            config = {"configurable": {"thread_id": thread_id}}
            for event in self.compiled_workflow.stream(initial_state, config=config):
                for key, value in event.items():
                    final_state.update(value)
                    yield {"node": key, "state": final_state}
                    
            # Build the pipeline trace model
            try:
                final_state["pipeline_trace"] = self.build_trace(thread_id, final_state)
            except Exception as e:
                logger.error(f"Failed to build pipeline trace: {e}")
                final_state["pipeline_trace"] = None
                
            yield {"node": "final", "state": final_state}
        except Exception as e:
            logger.error(f"Workflow execution failed: {e}")
            raise
            
    def build_trace(self, thread_id: str, final_state: dict):
        import uuid
        from datetime import datetime
        from utils.trace_model import PipelineTrace, RetrievalStatistics
        from config.settings import settings
        
        latencies = final_state.get("retrieval_trace", {}).get("latencies", {})
        total_latency = sum(latencies.values())
        
        failure_stage = final_state.get("failure_stage", FailureStage.NONE)
        first_failure_stage = final_state.get("first_failure_stage", FailureStage.NONE)
        failure_code = final_state.get("failure_code", FailureCode.NONE)
        failure_reasoning = final_state.get("failure_reasoning", "")
        
        retrieval_stats = final_state.get("retrieval_trace", {}).get("retrieval_statistics", {})
        
        import subprocess
        try:
            git_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL).decode("utf-8").strip()
        except Exception:
            git_commit = "unknown"
            
        trace_model = PipelineTrace(
            trace_id=str(uuid.uuid4()),
            thread_id=thread_id,
            query_id=str(uuid.uuid4())[:8],
            request_id=str(uuid.uuid4()),
            timestamp=datetime.now().isoformat(),
            git_commit=git_commit,
            query=final_state.get("question", ""),
            retrieval_strategy=final_state.get("planner_decision", {}),
            retrieval_statistics=RetrievalStatistics(**retrieval_stats) if retrieval_stats else RetrievalStatistics(),
            candidate_lifecycle=final_state.get("retrieval_trace", {}).get("candidate_lifecycle", []),
            latency=latencies,
            planner={"decision": final_state.get("planner_decision", {})},
            failure_stage=failure_stage,
            first_failure_stage=first_failure_stage,
            failure_code=failure_code,
            failure_reasoning=failure_reasoning,
            prompt_version=final_state.get("retrieval_trace", {}).get("prompt_version", "default_v1"),
            retrieval_candidates=retrieval_stats.get("raw_candidates", 0) if retrieval_stats else 0,
            retrieval_selected=retrieval_stats.get("after_rerank", 0) if retrieval_stats else 0,
            rerank_threshold=settings.CONFIDENCE_THRESHOLD_HIGH,
            latency_ms=total_latency * 1000
        )
        return trace_model

async def save_agent_caches(state: dict):
    turn_status = state.get("turn_status", "success")
    sanitized_answer = state.get("draft_answer", "")
    
    if turn_status == "success" and "I couldn't find enough evidence" not in sanitized_answer:
        decision = state.get("planner_decision", {})
        if decision.get("needs_retrieval", True):
            tenant_id = state.get("tenant_id", "default_tenant")
            thread_id = state.get("thread_id", "default_thread")
            standalone_query = state.get("current_query", state.get("question", ""))
            
            try:
                await exact_cache_manager.set_cache(standalone_query, sanitized_answer, tenant_id, thread_id)
                logger.info(f"[CACHE] Exact: Saved | Status: Verified | Reason: PASS")
                
                metadata = {
                    "planner_type": decision.get("query_type", "single_entity")
                }
                
                def sync_save_semantic(ver):
                    semantic_cache_manager.set_cache(standalone_query, sanitized_answer, tenant_id, thread_id, version=ver, metadata=metadata)
                    
                version = await exact_cache_manager.get_tenant_version(tenant_id)
                await asyncio.to_thread(sync_save_semantic, version)
                logger.info(f"[CACHE] Semantic: Saved | Status: Verified | Reason: PASS")
            except Exception as e:
                logger.error(f"[CACHE] Error saving caches: {e}")
    else:
        logger.info(f"[CACHE] Skipped | Reason: Verification failed or Turn Status is {turn_status}")