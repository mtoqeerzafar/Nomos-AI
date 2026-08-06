import os
import json
import logging
from pathlib import Path
from datetime import datetime
from utils.trace_model import PipelineTrace

logger = logging.getLogger(__name__)

class TraceRecorder:
    @staticmethod
    def save(trace: PipelineTrace):
        try:
            # We must wrap this in a top level try-except so tracing NEVER breaks API responses
            from config.settings import settings
            
            if not settings.ENABLE_TRACE_RECORDING:
                return

            # Determine the base log directory
            base_log_dir = Path("logs/traces")
            date_str = datetime.now().strftime("%Y-%m-%d")
            log_dir = base_log_dir / date_str
            log_dir.mkdir(parents=True, exist_ok=True)
            
            # Create a unique filename
            # Fallback to current timestamp if trace fields are missing
            file_id = trace.trace_id if trace.trace_id else trace.query_id
            if not file_id:
                file_id = datetime.now().strftime("%H%M%S")
                
            file_name = f"trace_{file_id}.json"
            file_path = log_dir / file_name
            
            # Serialize
            trace_dict = trace.model_dump()
            
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(trace_dict, f, ensure_ascii=False, indent=2)
                
            logger.info(f"Pipeline trace saved successfully: {file_path}")
            
        except Exception as e:
            logger.exception(f"Failed to save pipeline trace: {e}")
