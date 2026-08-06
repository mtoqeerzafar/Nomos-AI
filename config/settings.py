from dotenv import load_dotenv
load_dotenv(override=True)

from pydantic_settings import BaseSettings
from .constants import MAX_FILE_SIZE, MAX_TOTAL_SIZE, ALLOWED_TYPES
import os
import sys
import logging

# Reconfigure stdout and stderr to UTF-8 to handle Unicode characters on Windows
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass
    
    # Resolve Windows short 8.3 paths in TEMP/TMP (e.g. TOQEE_~1 -> toqee_41ka4vz)
    # to prevent ONNX Runtime path validation errors.
    from pathlib import Path
    for var in ["TEMP", "TMP"]:
        val = os.environ.get(var)
        if val:
            try:
                os.environ[var] = str(Path(val).resolve())
            except Exception:
                pass


# Disable anonymized telemetry warnings from ChromaDB
os.environ["ANONYMIZED_TELEMETRY"] = "False"
logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)

# Disable Hugging Face Hub symlinks on Windows to prevent ONNX Runtime path traversal validation errors
os.environ["HF_HUB_DISABLE_SYMLINKS"] = "1"


class Settings(BaseSettings):
    # Required settings - Changed from OPENAI_API_KEY to GOOGLE_API_KEY
    # Azure OpenAI Config (Primary Production Provider)
    AZURE_OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "")
    AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    AZURE_OPENAI_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
    AZURE_OPENAI_DEPLOYMENT_NAME: str = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt41mini")
    
    # Provider Switcher (azure, groq)
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "azure")
    
    # Groq Config (High-Speed Fallback Provider)
    GROQ_API_KEYS: str = os.getenv("GROQ_API_KEYS", "")

    # Optional settings with defaults
    MAX_FILE_SIZE: int = MAX_FILE_SIZE
    MAX_TOTAL_SIZE: int = MAX_TOTAL_SIZE
    ALLOWED_TYPES: list = ALLOWED_TYPES

    # Database settings
    CHROMA_DB_PATH: str = "./chroma_db"
    CHROMA_COLLECTION_NAME: str = "documents"

    # Ingestion & Embedding settings
    EMBEDDING_MODEL_NAME: str = os.getenv("EMBEDDING_MODEL_NAME", "intfloat/multilingual-e5-large")

    # Retrieval & Reranking settings
    RETRIEVAL_MODE: str = "adaptive_multi_strategy"
    VECTOR_SEARCH_K: int = 75
    METADATA_SEARCH_K: int = 50
    CANDIDATE_POOL_TOP_K: int = 100
    RERANKER_TOP_K: int = 15
    RERANKER_THRESHOLD: float = 0.0

    # Configurable Multi-Factor Score Boosts
    ARTICLE_MATCH_BOOST: float = 0.25
    LAW_MATCH_BOOST: float = 0.15
    YEAR_MATCH_BOOST: float = 0.05
    DOC_TYPE_MATCH_BOOST: float = 0.03
    DUAL_PROVIDER_PROVENANCE_BONUS: float = 0.10

    # Smart Neighbor Expansion Settings
    ENABLE_NEIGHBOR_EXPANSION: bool = True
    NEIGHBOR_EXPANSION_KEYWORDS: list = [
        "next", "previous", "following", "above", "below",
        "أعلاه", "أدناه", "التالية", "السابقة", "المقبلة", "اللاحقة"
    ]

    # Relationship Graph Settings
    RELATIONSHIP_DECAY_FACTOR: float = float(os.getenv("RELATIONSHIP_DECAY_FACTOR", "0.1"))
    RELATIONSHIP_CONFIDENCE_HUMAN: float = float(os.getenv("RELATIONSHIP_CONFIDENCE_HUMAN", "1.0"))
    RELATIONSHIP_CONFIDENCE_REGEX: float = float(os.getenv("RELATIONSHIP_CONFIDENCE_REGEX", "0.99"))
    RELATIONSHIP_CONFIDENCE_LLM: float = float(os.getenv("RELATIONSHIP_CONFIDENCE_LLM", "0.80"))
    MIN_RELATION_CONFIDENCE: float = float(os.getenv("MIN_RELATION_CONFIDENCE", "0.60"))
    GRAPH_EXPANSION_THRESHOLD: float = float(os.getenv("GRAPH_EXPANSION_THRESHOLD", "0.45"))

    # Chunking settings
    PARAGRAPH_BATCH_SIZE: int = 3
    MAX_TABLE_ROWS_PER_CHUNK: int = 10
    TABLE_ROW_BATCH_SIZE: int = 5
    
    # Confidence Thresholds
    CONFIDENCE_THRESHOLD_HIGH: float = 0.75
    CONFIDENCE_THRESHOLD_LOW: float = 0.40
    PLANNER_CONFIDENCE_THRESHOLD: float = float(os.getenv("PLANNER_CONFIDENCE_THRESHOLD", "0.70"))

    # Relevance Checker Settings & Thresholds
    RELEVANCE_COVERAGE_THRESHOLD: float = float(os.getenv("RELEVANCE_COVERAGE_THRESHOLD", "0.70"))
    RELEVANCE_ENTITY_THRESHOLD: float = float(os.getenv("RELEVANCE_ENTITY_THRESHOLD", "0.65"))
    RELEVANCE_ROLE_THRESHOLD: float = float(os.getenv("RELEVANCE_ROLE_THRESHOLD", "0.65"))
    RELEVANCE_CONFIDENCE_THRESHOLD: float = float(os.getenv("RELEVANCE_CONFIDENCE_THRESHOLD", "0.60"))
    MAX_RETRIEVAL_RETRIES: int = int(os.getenv("MAX_RETRIEVAL_RETRIES", "2"))

    # Logging settings
    LOG_LEVEL: str = "INFO"

    # Observability & Debug settings
    DEBUG_RETRIEVAL: bool = False
    ENABLE_RETRIEVAL_TRACE: bool = False
    ENABLE_CACHE_TRACE: bool = False
    ENABLE_WORKFLOW_TRACE: bool = False
    
    # TraceRecorder Observability config
    ENABLE_TRACE_RECORDING: bool = os.getenv("ENABLE_TRACE_RECORDING", "true").lower() == "true"
    TRACE_INCLUDE_TEXT: bool = os.getenv("TRACE_INCLUDE_TEXT", "false").lower() == "true"
    TRACE_MAX_CANDIDATES: int = int(os.getenv("TRACE_MAX_CANDIDATES", "30"))

    # Cache settings with type annotations
    CACHE_DIR: str = "document_cache"
    CACHE_EXPIRE_DAYS: int = 7

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()