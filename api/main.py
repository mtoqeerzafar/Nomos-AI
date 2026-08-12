import uvicorn
from fastapi import FastAPI, Depends, HTTPException, Header, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import json
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager
import redis.asyncio as redis
from fastapi_limiter import FastAPILimiter
from fastapi_limiter.depends import RateLimiter
from pydantic import BaseModel
from psycopg_pool import ConnectionPool
from langgraph.checkpoint.postgres import PostgresSaver

from db.database import Base, engine, get_db, DATABASE_URL
from db.models import (
    DocumentJob, ChatMessage, ChatThread, 
    DocumentFamily, Document, DocumentRelationship, 
    AuthorityRank, OrganizationGlossary
)
from storage.s3_client import s3_client
from workers.tasks import process_document_task

from agents.workflow import AgentWorkflow, save_agent_caches
from retriever.builder import RetrieverBuilder
from utils.observability import setup_observability
from utils.memory import memory_manager
import uuid
import asyncio
import logging
from cache.exact_cache import exact_cache_manager
from cache.semantic_cache import semantic_cache_manager

logger = logging.getLogger(__name__)

def log_query_evolution(query: str, state: dict):
    if not state:
        return
    planner = state.get("planner_decision", {})
    needs_history = planner.get("needs_chat_history", "N/A")
    standalone = state.get("current_query", "N/A")
    retrieval_queries = "\n".join(state.get("retrieval_queries", []))
    docs_len = len(state.get("documents", []))
    relevance = state.get("relevance_result", {}).get("sufficient", "N/A")
    relevance_str = "PASS" if relevance is True else ("FAIL" if relevance is False else "N/A")
    report = state.get("verification_report", "")
    verification_str = "FAIL" if "**Supported:** NO" in report or "**Relevant:** NO" in report else ("PASS" if report else "N/A")
    status = state.get("turn_status", "success")
    
    evolution_log = f"\n========================\nREQUEST\n========================\nRaw:\n{query}\n\nPlanner:\nneeds_chat_history={needs_history}\n\nStandalone:\n{standalone}\n\nRetrieval:\n{retrieval_queries}\n\nRetriever:\n{docs_len} docs\n\nRelevance:\n{relevance_str}\n\nVerification:\n{verification_str}\n\nStatus:\n{status}\n========================"
    logger.info(evolution_log)

# Global RAG Components
workflow = None
checkpointer_pool = None
checkpointer = None
retriever_builder = RetrieverBuilder()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize Observability
    setup_observability()
    
    # Initialize database tables
    Base.metadata.create_all(bind=engine)
    
    # Ensure default user exists to satisfy foreign key constraints
    from db.models import User
    from sqlalchemy import text
    with Session(engine) as session:
        if not session.query(User).filter_by(id="default_tenant").first():
            session.add(User(id="default_tenant", email="default@example.com", hashed_password="dummy"))
            session.commit()
            
        # Alter document_jobs to add thread_id safely if it doesn't exist
        try:
            if engine.dialect.name == "sqlite":
                session.execute(text("ALTER TABLE document_jobs ADD COLUMN thread_id VARCHAR;"))
            else:
                session.execute(text("ALTER TABLE document_jobs ADD COLUMN IF NOT EXISTS thread_id VARCHAR;"))
            session.commit()
        except Exception:
            session.rollback()
            
    # Initialize PostgreSQL Checkpointer for LangGraph (with MemorySaver fallback)
    global checkpointer_pool, checkpointer, workflow
    conn_url = (DATABASE_URL.replace("+psycopg2", "").replace("+psycopg", "") if DATABASE_URL and "postgresql" in DATABASE_URL else "postgresql://postgres:postgres@localhost:5432/ragnrai")
    
    try:
        import psycopg
        with psycopg.connect(conn_url, autocommit=True, connect_timeout=3) as conn:
            PostgresSaver(conn).setup()
            
        checkpointer_pool = ConnectionPool(conninfo=conn_url)
        checkpointer = PostgresSaver(checkpointer_pool)
        logger.info("LangGraph initialized with PostgreSQL Checkpointer.")
    except Exception as e:
        logger.warning(f"PostgreSQL Checkpointer connection failed ({e}). Using in-memory MemorySaver checkpointer.")
        from langgraph.checkpoint.memory import MemorySaver
        checkpointer = MemorySaver()

    workflow = AgentWorkflow(checkpointer=checkpointer)
    
    # Initialize Redis for rate limiting
    # redis_client = redis.from_url("redis://localhost:6379", encoding="utf-8", decode_responses=True)
    # await FastAPILimiter.init(redis_client)
    
    yield
    
    # Cleanup
    # await redis_client.close()
    if checkpointer_pool:
        checkpointer_pool.close()

app = FastAPI(title="RagnrAI API", lifespan=lifespan)

# Add CORS middleware for Next.js frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Allow any IP during dev
    allow_credentials=False, # Must be False when allow_origins=["*"] to prevent browser Preflight errors
    allow_methods=["*"],
    allow_headers=["*"],
)

class Attachment(BaseModel):
    id: str
    name: str

class QueryRequest(BaseModel):
    question: str
    thread_id: str = "default_thread" # Now accepts thread_id
    attachments: list[Attachment] = []

class ThreadCreateRequest(BaseModel):
    title: str = "New Chat"

class ThreadRenameRequest(BaseModel):
    title: str

@app.get("/api/upload-url")
def get_upload_url(filename: str):
    """Get a pre-signed URL to upload a document directly to S3."""
    url = s3_client.generate_presigned_upload_url(filename)
    if not url:
        raise HTTPException(status_code=500, detail="Could not generate upload URL")
    return {"upload_url": url, "s3_key": filename}

@app.post("/api/process")
def process_document(
    s3_key: str, 
    thread_id: str = None,
    x_tenant_id: str = Header(default="default_tenant"),
    db: Session = Depends(get_db)
):
    """Trigger background processing for an uploaded document."""
    job = DocumentJob(s3_key=s3_key, tenant_id=x_tenant_id, thread_id=thread_id)
    db.add(job)
    db.commit()
    db.refresh(job)
    
    # Trigger celery task
    process_document_task.delay(job.id, s3_key)
    
    return {"job_id": job.id, "status": job.status, "tenant_id": job.tenant_id}

@app.get("/api/status/{job_id}")
def get_status(job_id: str, db: Session = Depends(get_db)):
    """Check the status of a document processing job."""
    job = db.query(DocumentJob).filter(DocumentJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return {"job_id": job.id, "status": job.status, "error": job.error_message}

@app.delete("/api/documents/{job_id}")
def delete_document(
    job_id: str,
    x_tenant_id: str = Header(default="default_tenant"),
    db: Session = Depends(get_db)
):
    """Fully delete a document: removes from MinIO, Qdrant, and database."""
    job = db.query(DocumentJob).filter(
        DocumentJob.id == job_id,
        DocumentJob.tenant_id == x_tenant_id
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Document job not found")

    s3_key = job.s3_key

    # 1. Delete from MinIO storage
    try:
        s3_client.delete_file(s3_key)
    except Exception as e:
        logger.warning(f"Could not delete {s3_key} from MinIO: {e}")

    # 2. Delete chunks from Qdrant by filtering on s3_key + tenant_id
    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        from db.qdrant_client import qdrant_manager
        qdrant_manager.client.delete(
            collection_name=qdrant_manager.collection_name,
            points_selector=Filter(
                must=[
                    FieldCondition(key="s3_key", match=MatchValue(value=s3_key)),
                    FieldCondition(key="tenant_id", match=MatchValue(value=x_tenant_id)),
                ]
            )
        )
    except Exception as e:
        logger.warning(f"Could not delete Qdrant vectors for {s3_key}: {e}")

    # 3. Delete Document family/version records linked to this job's s3_key
    try:
        from db.models import Document, DocumentFamily
        matching_docs = db.query(Document).filter(
            Document.uploaded_by == x_tenant_id
        ).all()
        for doc in matching_docs:
            family = db.query(DocumentFamily).filter(DocumentFamily.id == doc.document_family_id).first()
            if family and (s3_key in family.title or family.title in s3_key):
                db.delete(doc)
        db.commit()
    except Exception as e:
        logger.warning(f"Could not delete Document records for {s3_key}: {e}")

    # 4. Remove attachment entry from ChatMessage.metadata_json in the same thread
    # so that chips don't reappear after a page refresh.
    try:
        thread_messages = db.query(ChatMessage).filter(
            ChatMessage.thread_id == job.thread_id
        ).all() if job.thread_id else []

        for chat_msg in thread_messages:
            meta = chat_msg.metadata_json
            if not meta:
                continue
            attachments = meta.get("attachments", [])
            cleaned = [a for a in attachments if a.get("id") != job_id]
            if len(cleaned) != len(attachments):
                # Use flag_modified so SQLAlchemy detects the mutable JSON change
                chat_msg.metadata_json = {**meta, "attachments": cleaned}
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(chat_msg, "metadata_json")

        db.commit()
    except Exception as e:
        logger.warning(f"Could not scrub attachment {job_id} from chat messages: {e}")

    # 5. Delete the DocumentJob itself
    db.delete(job)
    db.commit()

    return {"message": f"Document '{s3_key}' has been fully removed."}

# ==========================================
# THREAD MANAGEMENT API (Phase 10.3)
# ==========================================

@app.post("/api/threads")
def create_thread(
    request: ThreadCreateRequest,
    x_tenant_id: str = Header(default="default_tenant"),
    db: Session = Depends(get_db)
):
    """Create a new chat thread for the user."""
    thread = ChatThread(user_id=x_tenant_id, title=request.title)
    db.add(thread)
    db.commit()
    db.refresh(thread)
    return {"thread_id": thread.id, "title": thread.title, "created_at": thread.created_at}

@app.get("/api/threads")
def list_threads(
    x_tenant_id: str = Header(default="default_tenant"),
    db: Session = Depends(get_db)
):
    """List all chat threads for the user."""
    threads = db.query(ChatThread).filter(ChatThread.user_id == x_tenant_id).order_by(ChatThread.updated_at.desc()).all()
    return [{"thread_id": t.id, "title": t.title, "updated_at": t.updated_at} for t in threads]

@app.get("/api/threads/{thread_id}/messages")
def get_thread_messages(
    thread_id: str,
    x_tenant_id: str = Header(default="default_tenant"),
    db: Session = Depends(get_db)
):
    """Get all messages in a specific thread."""
    thread = db.query(ChatThread).filter(ChatThread.id == thread_id, ChatThread.user_id == x_tenant_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    messages = db.query(ChatMessage).filter(ChatMessage.thread_id == thread_id).order_by(ChatMessage.created_at.asc()).all()
    return [{
        "id": m.id, 
        "role": m.role, 
        "content": m.content, 
        "created_at": m.created_at,
        "attachments": m.metadata_json.get("attachments", []) if m.metadata_json else [],
        "sources": m.metadata_json.get("sources", []) if m.metadata_json else []
    } for m in messages]

@app.get("/api/threads/{thread_id}/documents")
def get_thread_documents(
    thread_id: str,
    x_tenant_id: str = Header(default="default_tenant"),
    db: Session = Depends(get_db)
):
    """Get all successfully processed documents for a specific thread."""
    jobs = db.query(DocumentJob).filter(
        DocumentJob.thread_id == thread_id,
        DocumentJob.tenant_id == x_tenant_id,
        DocumentJob.status == "COMPLETED"
    ).all()
    return [{"id": j.id, "name": j.s3_key} for j in jobs]

@app.put("/api/threads/{thread_id}")
def rename_thread(
    thread_id: str,
    request: ThreadRenameRequest,
    x_tenant_id: str = Header(default="default_tenant"),
    db: Session = Depends(get_db)
):
    """Rename a specific chat thread."""
    thread = db.query(ChatThread).filter(ChatThread.id == thread_id, ChatThread.user_id == x_tenant_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    thread.title = request.title
    db.commit()
    return {"message": "Thread renamed successfully", "title": thread.title}

@app.delete("/api/threads/{thread_id}")
def delete_thread(
    thread_id: str,
    x_tenant_id: str = Header(default="default_tenant"),
    db: Session = Depends(get_db)
):
    """Delete a specific chat thread."""
    thread = db.query(ChatThread).filter(ChatThread.id == thread_id, ChatThread.user_id == x_tenant_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    db.delete(thread)
    db.commit()
    return {"message": "Thread deleted successfully"}

# ==========================================
# QUERY API
# ==========================================

@app.post("/api/query/v2")
def query_rag_v2(
    request: QueryRequest,
    x_tenant_id: str = Header(default="default_tenant"),
    db: Session = Depends(get_db)
):
    """Query the RAG pipeline with thread history memory and LangGraph persistence."""
    retriever = retriever_builder.build_hybrid_retriever(tenant_id=x_tenant_id, thread_id=request.thread_id)
    
    # 1. Ensure Thread Exists
    thread = db.query(ChatThread).filter(ChatThread.id == request.thread_id).first()
    if not thread:
        thread = ChatThread(
            id=request.thread_id,
            user_id=x_tenant_id,
            title=request.question[:50] + "..." if len(request.question) > 50 else request.question
        )
        db.add(thread)
        db.commit()

    # 2. Save User Message
    user_msg = ChatMessage(
        thread_id=request.thread_id, 
        role="user", 
        content=request.question,
        metadata_json={"attachments": [a.model_dump() for a in request.attachments]} if request.attachments else None
    )
    db.add(user_msg)
    db.commit()
    
    # 2. Fetch history and apply Memory Manager Summarization Policy
    past_messages = db.query(ChatMessage).filter(ChatMessage.thread_id == request.thread_id).order_by(ChatMessage.created_at.asc()).all()
    condensed_history = memory_manager.compress_history(past_messages[:-1]) # exclude the just added one
    
    # 3. Run the query through the multi-agent workflow
    try:
        pipeline_generator = workflow.full_pipeline(
            question=request.question,
            retriever=retriever,
            chat_history=condensed_history,
            thread_id=request.thread_id,
            tenant_id=x_tenant_id,
            attachments=[a.model_dump() for a in request.attachments]
        )
        
        # Exhaust the generator to get the final state
        final_state = None
        for event in pipeline_generator:
            final_state = event["state"]
                
        if not final_state:
            raise Exception("Workflow did not complete")
            
        # 4. Save AI Response
        status = final_state.get("turn_status", "success")
        sources = extract_sources_from_state(final_state)
        metadata_json = {"verification_report": final_state.get("verification_report", ""), "status": status, "sources": sources}
        ai_msg = ChatMessage(
            thread_id=request.thread_id, 
            role="assistant", 
            content=final_state["draft_answer"],
            metadata_json=metadata_json
        )
        db.add(ai_msg)
        db.commit()
        
        log_query_evolution(request.question, final_state)
        
        if final_state.get("pipeline_trace"):
            try:
                from utils.trace_recorder import TraceRecorder
                TraceRecorder.save(final_state["pipeline_trace"])
            except Exception as e:
                logger.error(f"Error saving pipeline trace in /api/query: {e}")
        
        certified = final_state.get("certified_response")
        resp_output = final_state.get("response_output")
        return {
            "answer": final_state["draft_answer"],
            "verification_report": final_state["verification_report"],
            "sources": sources,
            "tenant_id": x_tenant_id,
            "thread_id": request.thread_id,
            "response_output": resp_output.model_dump() if resp_output and hasattr(resp_output, "model_dump") else None,
            "certified_response": certified.model_dump() if certified and hasattr(certified, "model_dump") else None
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

def extract_sources_from_state(final_state: dict) -> list:
    """Robust helper to extract non-empty source citations across all pipeline state formats."""
    sources = []
    if not final_state or final_state.get("turn_status") == "no_answer":
        return sources

    # 1. Extract from state.documents list
    docs = final_state.get("documents") or []
    for doc in docs:
        src = None
        if hasattr(doc, "metadata") and isinstance(doc.metadata, dict):
            src = doc.metadata.get("source")
        elif isinstance(doc, dict):
            meta = doc.get("metadata", {})
            src = meta.get("source") if isinstance(meta, dict) else doc.get("source")
        if src and src not in sources:
            sources.append(src)

    # 2. Extract from response_output citations
    resp_output = final_state.get("response_output")
    if not sources and resp_output:
        citations = getattr(resp_output, "citations", []) or (resp_output.get("citations", []) if isinstance(resp_output, dict) else [])
        for cit in citations:
            law_title = getattr(cit, "law_title", None) or (cit.get("law_title") if isinstance(cit, dict) else None)
            formatted = getattr(cit, "formatted", None) or (cit.get("formatted") if isinstance(cit, dict) else str(cit))
            src = law_title or formatted
            if src and src not in sources:
                sources.append(src)

    # 3. Extract from certified_response
    certified = final_state.get("certified_response")
    if not sources and certified:
        resp = getattr(certified, "response", None) or (certified.get("response") if isinstance(certified, dict) else None)
        if resp:
            citations = getattr(resp, "citations", []) or (resp.get("citations", []) if isinstance(resp, dict) else [])
            for cit in citations:
                law_title = getattr(cit, "law_title", None) or (cit.get("law_title") if isinstance(cit, dict) else None)
                formatted = getattr(cit, "formatted", None) or (cit.get("formatted") if isinstance(cit, dict) else str(cit))
                src = law_title or formatted
                if src and src not in sources:
                    sources.append(src)

    # 4. Extract from generation_artifacts
    artifacts = final_state.get("generation_artifacts")
    if not sources and artifacts and isinstance(artifacts, dict):
        bound = artifacts.get("citations_bound") or []
        for b in bound:
            if b and str(b) not in sources:
                sources.append(str(b))

    return sources

@app.post("/api/query/stream")
async def stream_query(
    api_request: Request,
    query_req: QueryRequest,
    x_tenant_id: str = Header(default="default_tenant"),
    db: Session = Depends(get_db)
):
    """Query the RAG pipeline with SSE streaming and connection lost handling."""
    retriever = retriever_builder.build_hybrid_retriever(tenant_id=x_tenant_id, thread_id=query_req.thread_id)
    
    # Ensure Thread Exists
    thread = db.query(ChatThread).filter(ChatThread.id == query_req.thread_id).first()
    if not thread:
        thread = ChatThread(
            id=query_req.thread_id,
            user_id=x_tenant_id,
            title=query_req.question[:50] + "..." if len(query_req.question) > 50 else query_req.question
        )
        db.add(thread)
        db.commit()

    user_msg = ChatMessage(
        thread_id=query_req.thread_id, 
        role="user", 
        content=query_req.question,
        metadata_json={"attachments": [a.model_dump() for a in query_req.attachments]} if query_req.attachments else None
    )
    db.add(user_msg)
    db.commit()
    
    past_messages = db.query(ChatMessage).filter(ChatMessage.thread_id == query_req.thread_id).order_by(ChatMessage.created_at.asc()).all()
    
    async def event_generator():
        try:
            condensed_history = await asyncio.to_thread(memory_manager.compress_history, past_messages[:-1])


            initial_state = {
                "question": query_req.question,
                "chat_history": condensed_history,
                "tenant_id": x_tenant_id,
                "thread_id": query_req.thread_id,
                "attachments": [a.model_dump() for a in query_req.attachments] if query_req.attachments else [],
                "current_query": query_req.question,
                "draft_answer": "",
                "verification_report": "",
                "failure_reason": "NONE",
                "confidence_score": 0.0,
                "retriever": retriever,
                "revision_count": 0,
                "retrieval_attempts": 0,
                "feedback": "",
                "used_cache": True,
                "documents": [],
                "cached_documents": None,
                "planner_decision": {}
            }

            config = {"configurable": {"thread_id": query_req.thread_id}}
            
            final_answer = ""
            final_state = {}
            current_node = None
            stream_workflow = AgentWorkflow(checkpointer=None)
            
            STATUS_MAP = {
                "plan": ("planning", "Planning strategy...", 10),
                "retrieve": ("retrieval", "Searching documents...", 30),
                "check_cache": ("cache_check", "Checking cache...", 20),
                "rerank": ("ranking", "Ranking evidence...", 45),
                "research": ("analysis", "Analyzing evidence...", 65),
                "verify": ("verification", "Verifying facts...", 85),
                "output_guardrail": ("finalizing", "Preparing response...", 95),
                "_output_guardrail_step": ("finalizing", "Preparing response...", 95)
            }
            
            async for event in stream_workflow.compiled_workflow.astream_events(
                initial_state, 
                config=config,
                version="v2"
            ):
                if await api_request.is_disconnected():
                    print(f"[WARN] Client disconnected during stream for thread {query_req.thread_id}")
                    break

                kind = event["event"]
                name = event.get("name", "")
                
                if kind == "on_chain_end" and name == "LangGraph":
                    final_state = event["data"].get("output", {})
                
                # Stream workflow progress events
                if kind == "on_chain_start":
                    if name in STATUS_MAP:
                        stage, msg, prog = STATUS_MAP[name]
                        data = json.dumps({"type": "status", "stage": stage, "message": msg, "progress": prog})
                        yield f"data: {data}\n\n"
                    elif name == "chat_responder":
                        data = json.dumps({"type": "status", "stage": "chat", "message": "Formulating response...", "progress": 80})
                        yield f"data: {data}\n\n"
            
            final_answer = ""
            sources = []
            if final_state:
                final_answer = final_state.get("draft_answer", "")
                if not final_answer and final_state.get("messages"):
                    final_answer = final_state["messages"][-1].content
                sources = extract_sources_from_state(final_state)
                    
            # Save assistant message after stream finishes
            if final_answer:
                status = final_state.get("turn_status", "success") if final_state else "success"
                metadata_json = {"status": status, "sources": sources}
                if final_state and final_state.get("verification_report"):
                    metadata_json["verification_report"] = final_state.get("verification_report")
                    
                assistant_msg = ChatMessage(
                    thread_id=query_req.thread_id, 
                    role="assistant", 
                    content=final_answer,
                    metadata_json=metadata_json
                )
                db.add(assistant_msg)
                db.commit()
                
                log_query_evolution(query_req.question, final_state)
                
            # Generate trace for streaming workflow
            if final_state:
                try:
                    final_state["pipeline_trace"] = stream_workflow.build_trace(query_req.thread_id, final_state)
                except Exception as e:
                    logger.error(f"Failed to build trace in stream: {e}")
                    
            # Now stream the verified answer to the user
            yield f"data: {json.dumps({'type': 'answer_start'})}\n\n"
            
            # Start cache save task but await it after streaming
            cache_task = None
            if final_state:
                cache_task = asyncio.create_task(save_agent_caches(final_state))
                
                # Also save the trace
                if final_state.get("pipeline_trace"):
                    try:
                        from utils.trace_recorder import TraceRecorder
                        TraceRecorder.save(final_state["pipeline_trace"])
                    except Exception as e:
                        logger.error(f"Failed to save pipeline trace in stream: {e}")
                
            # Simulate streaming word by word
            words = final_answer.split(" ")
            for i, word in enumerate(words):
                chunk = word + (" " if i < len(words) - 1 else "")
                yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"
                await asyncio.sleep(0.02)
                
            certified = final_state.get("certified_response") if final_state else None
            certified_dict = certified.model_dump() if certified and hasattr(certified, "model_dump") else None
            yield f"data: {json.dumps({'type': 'answer_end', 'sources': sources, 'certified_response': certified_dict})}\n\n"
            yield f"data: {json.dumps({'node': 'final', 'answer': final_answer, 'sources': sources, 'certified_response': certified_dict})}\n\n"
            
            if cache_task:
                await cache_task

        except Exception as e:
            import traceback
            traceback.print_exc()
            db.rollback()
            error_payload = json.dumps({"error": str(e)})
            yield f"data: {error_payload}\n\n"
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")

if __name__ == "__main__":
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
