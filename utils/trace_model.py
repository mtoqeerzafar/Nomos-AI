from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any
from enum import Enum

class FailureStage(str, Enum):
    NONE = "NONE"
    RETRIEVAL = "RETRIEVAL"
    RERANKING = "RERANKING"
    RELEVANCE = "RELEVANCE"
    REWRITE = "REWRITE"
    GENERATION = "GENERATION"
    VERIFICATION = "VERIFICATION"

class FailureCode(str, Enum):
    NONE = "NONE"
    NO_DOCUMENTS = "NO_DOCUMENTS"
    BELOW_THRESHOLD = "BELOW_THRESHOLD"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    REWRITE_FAILED = "REWRITE_FAILED"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    UNSUPPORTED = "UNSUPPORTED"

class ChunkTrace(BaseModel):
    id: str
    source: Optional[str] = None
    article_number: Optional[str] = None
    chunk_index: Optional[int] = None
    document_text: Optional[str] = None # Redacted by default
    
    # Ranking & Scores
    bm25_rank: Optional[int] = None
    bm25_score: Optional[float] = None
    dense_rank: Optional[int] = None
    dense_score: Optional[float] = None
    rrf_rank: Optional[int] = None
    rerank_score: Optional[float] = None
    
    # Origin & Positioning
    retrieval_origin: Optional[str] = None # e.g., "BM25", "Dense", "Hybrid"
    chunk_position: Optional[int] = None
    
    # Lifecycle
    retrieved: bool = False
    reranked: bool = False
    selected: bool = False
    sent_to_generator: bool = False
    
    drop_reason: Optional[str] = None
    threshold: Optional[float] = None
    decision: Optional[str] = None

class RetrievalStatistics(BaseModel):
    raw_candidates: int = 0
    after_grouping: int = 0
    after_rerank: int = 0
    after_relevance: int = 0
    generator_context: int = 0
    
    average_rerank_score: float = 0.0
    max_rerank_score: float = 0.0
    min_rerank_score: float = 0.0
    std_dev_rerank_score: float = 0.0
    chunks_above_threshold: int = 0
    chunks_below_threshold: int = 0
    
    gold_retrieval_recall: Optional[float] = None
    gold_survival: Optional[float] = None
    gold_usage: Optional[float] = None

class PipelineTrace(BaseModel):
    # Metadata
    trace_schema_version: int = 1
    trace_id: str
    thread_id: str
    query_id: str
    request_id: str
    timestamp: str
    git_commit: str = "unknown"
    pipeline_version: str = "2.1"
    
    # Core
    query: str
    
    # Strategy
    retrieval_strategy: Dict[str, Any] = Field(default_factory=dict)
    
    # Stats
    retrieval_statistics: RetrievalStatistics = Field(default_factory=RetrievalStatistics)
    
    # Lifecycle
    candidate_lifecycle: List[ChunkTrace] = Field(default_factory=list)
    
    # Latency
    latency: Dict[str, float] = Field(default_factory=dict)
    
    # Sub-node state copies
    planner: Dict[str, Any] = Field(default_factory=dict)
    rewrite: Dict[str, Any] = Field(default_factory=dict)
    relevance: Dict[str, Any] = Field(default_factory=dict)
    verification: Dict[str, Any] = Field(default_factory=dict)
    
    prompt_version: str = "default_v1"
    
    # Evaluation
    evaluation: Dict[str, Any] = Field(default_factory=lambda: {
        "expected_answer_available": None,
        "human_label": None,
        "pass": None
    })
    
    # Final Outcome
    failure_stage: FailureStage = FailureStage.NONE
    first_failure_stage: FailureStage = FailureStage.NONE
    failure_code: FailureCode = FailureCode.NONE
    failure_reasoning: str = ""
    # Flat metrics for analytics
    retrieval_candidates: int = 0
    retrieval_selected: int = 0
    rerank_threshold: float = 0.7
    latency_ms: float = 0.0
