import os
from qdrant_client import QdrantClient
from qdrant_client.models import ScalarQuantization, ScalarQuantizationConfig, ScalarType
from utils.logging import logger
from dotenv import load_dotenv
from config.settings import settings

load_dotenv()

class QdrantManager:
    def __init__(self):
        self.url = os.getenv("QDRANT_URL", "http://localhost:6333")
        self.collection_name = "ragnr_documents"
        self.semantic_cache_name = "semantic_cache"
        self._models_loaded = False
        
        # Connect to Qdrant using gRPC with fallback to HTTP if unavailable
        # and checking 127.0.0.1 in case localhost IPv6 resolution is refused
        connected = False
        candidates = [self.url]
        if "localhost" in self.url:
            candidates.append(self.url.replace("localhost", "127.0.0.1"))
            
        for url_candidate in candidates:
            try:
                logger.info(f"Attempting connection to Qdrant (gRPC) at {url_candidate}...")
                self.client = QdrantClient(url=url_candidate, prefer_grpc=True)
                self.client.collection_exists("test_dummy_conn")
                self.url = url_candidate
                logger.info("Connected to Qdrant via gRPC.")
                connected = True
                break
            except Exception as e:
                logger.warning(f"Qdrant gRPC connection failed at {url_candidate}: {e}")
                
            try:
                logger.info(f"Attempting connection to Qdrant (HTTP) at {url_candidate}...")
                self.client = QdrantClient(url=url_candidate, prefer_grpc=False)
                self.client.collection_exists("test_dummy_conn")
                self.url = url_candidate
                logger.info("Connected to Qdrant via HTTP.")
                connected = True
                break
            except Exception as e:
                logger.warning(f"Qdrant HTTP connection failed at {url_candidate}: {e}")
                
        if not connected:
            qdrant_db_path = os.path.join(os.getcwd(), "cache", "qdrant_storage")
            os.makedirs(qdrant_db_path, exist_ok=True)
            logger.info(f"Could not connect to Qdrant server. Using persistent local disk storage at: {qdrant_db_path}")
            self.client = QdrantClient(path=qdrant_db_path)
            
        self._ensure_collection()



    def load_models(self):
        if not self._models_loaded:
            logger.info("Loading FastEmbed models into System RAM...")
            providers = ["CPUExecutionProvider"]
            
            # Disable HF symlinks on Windows globally before loading
            os.environ["HF_HUB_DISABLE_SYMLINKS"] = "1"
            
            # Set a persistent cache directory for FastEmbed to avoid temp folder wipes
            persistent_cache = "d:/RagnrAI/cache/fastembed_cache"
            os.environ["FASTEMBED_CACHE_PATH"] = persistent_cache
            os.makedirs(persistent_cache, exist_ok=True)
                
            # Configure FastEmbed local models for Dense and Sparse embeddings dynamically
            logger.info(f"Using dense model: {settings.EMBEDDING_MODEL_NAME}")
            try:
                self.client.set_model(settings.EMBEDDING_MODEL_NAME, providers=providers)
            except Exception as load_err:
                # Check if this is a RAM exhaustion issue to prevent deleting good model files
                is_mem_error = any(x in str(load_err).lower() for x in ["bad allocation", "memoryerror", "out of memory"])
                if is_mem_error:
                    logger.error("System RAM is completely exhausted. Please close other heavy programs and restart the server.")
                    raise load_err
                    
                logger.warning(f"Failed to load model from cache ({load_err}). Attempting clean download...")
                
                # Resolve cache path for this model
                model_suffix = settings.EMBEDDING_MODEL_NAME.split("/")[-1] + "-onnx"
                model_dir = os.path.join(persistent_cache, f"models--qdrant--{model_suffix}")
                
                # Clean up corrupted model folder if exists
                if os.path.exists(model_dir):
                    logger.info(f"Cleaning corrupted cache directory: {model_dir}")
                    import shutil
                    shutil.rmtree(model_dir, ignore_errors=True)
                
                # Perform snapshot download programmatically
                from huggingface_hub import snapshot_download
                logger.info(f"Downloading model snapshot for qdrant/{model_suffix}...")
                snapshot_download(
                    repo_id=f"qdrant/{model_suffix}",
                    cache_dir=persistent_cache,
                    max_workers=4
                )
                
                # Retry loading model
                self.client.set_model(settings.EMBEDDING_MODEL_NAME, providers=providers)

            self._models_loaded = True
            logger.info("Dense embedding model loaded successfully.")


    def _ensure_collection(self):
        self.load_models()
        raw_vectors = self.client.get_fastembed_vector_params()
        
        expected_vectors = {
            self.client.get_vector_field_name(): list(raw_vectors.values())[0]
        } if raw_vectors else {}
        
        # Ensure main collection (Create if missing; never auto-delete existing indexed data)
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=expected_vectors,
                quantization_config=ScalarQuantization(
                    scalar=ScalarQuantizationConfig(
                        type=ScalarType.INT8,
                        always_ram=True
                    )
                )
            )
            logger.info("Dense collection created successfully.")
            
        # Ensure semantic cache collection
        recreate_cache = False
        if self.client.collection_exists(self.semantic_cache_name):
            info = self.client.get_collection(self.semantic_cache_name)
            current_vectors = info.config.params.vectors
            
            if not isinstance(current_vectors, dict):
                recreate_cache = True
            else:
                for name, expected_params in expected_vectors.items():
                    if name not in current_vectors or current_vectors[name].size != expected_params.size:
                        recreate_cache = True
                        break
        else:
            recreate_cache = True
            
        if recreate_cache:
            if self.client.collection_exists(self.semantic_cache_name):
                logger.info(f"Recreating Qdrant collection due to model changes: {self.semantic_cache_name}")
                self.client.delete_collection(self.semantic_cache_name)
                
            self.client.create_collection(
                collection_name=self.semantic_cache_name,
                vectors_config=expected_vectors,
                quantization_config=ScalarQuantization(
                    scalar=ScalarQuantizationConfig(
                        type=ScalarType.INT8,
                        always_ram=True
                    )
                )
            )
            logger.info("Semantic Cache collection created successfully.")

qdrant_manager = QdrantManager()

