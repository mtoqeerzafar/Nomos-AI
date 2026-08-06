import os
import gc
from pathlib import Path
from workers.celery_app import celery_app
from storage.s3_client import s3_client
from document_processor.file_handler import DocumentProcessor
from db.database import SessionLocal
from db.models import DocumentJob, Document, DocumentFamily, DocumentRelationship
from utils.logging import logger
from document_processor.metadata import extract_document_metadata, resolve_law_reference

processor = DocumentProcessor()

@celery_app.task(bind=True, max_retries=3)
def process_document_task(self, job_id: str, s3_key: str):
    db = SessionLocal()
    job = db.query(DocumentJob).filter(DocumentJob.id == job_id).first()
    
    if not job:
        logger.error(f"Job {job_id} not found.")
        db.close()
        return False
        
    job.status = "PROCESSING"
    db.commit()

    temp_dir = Path("/tmp/ragnr") if os.name != 'nt' else Path("C:/tmp/ragnr")
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # We download the file to process locally
    local_file_path = temp_dir / s3_key
    
    try:
        # Download from S3
        success = s3_client.download_file(s3_key, str(local_file_path))
        if not success:
            raise Exception("Failed to download file from S3")
            
        # Process the file (Extract markdown and split into chunks)
        chunks = processor.process_single_file(local_file_path)
        
        # Compile full text to extract metadata
        full_text = "\n".join([chunk.page_content for chunk in chunks])
        
        # Extract Layered Metadata & Relationships
        meta = extract_document_metadata(full_text, local_file_path.name)
        
        # 1. Resolve or create DocumentFamily
        family_title = meta["title"]
        domain = meta["domain"]
        
        family = db.query(DocumentFamily).filter(
            DocumentFamily.title == family_title,
            DocumentFamily.tenant_id == job.tenant_id
        ).first()
        
        if not family:
            family = DocumentFamily(
                title=family_title,
                domain=domain,
                tenant_id=job.tenant_id
            )
            db.add(family)
            db.commit()
            db.refresh(family)
            
        # 2. Determine previous version document ID (DAG helper)
        prev_doc = db.query(Document).filter(
            Document.document_family_id == family.id,
            Document.lifecycle_status == "Active"
        ).order_by(Document.uploaded_at.desc()).first()
        
        prev_id = prev_doc.id if prev_doc else None
        
        # Determine status (if warnings, set to Pending_Review)
        status = "Pending_Review" if meta["warnings"] else "Active"
        
        # 3. Create Document entry
        document = Document(
            id=job.id, # Link directly to job.id
            document_family_id=family.id,
            version=meta["version"],
            lifecycle_status=status,
            allowed_roles=meta["allowed_roles"],
            applicability=meta["applicability"],
            original_calendar=meta["original_calendar"],
            original_effective_date=meta["original_effective_date"],
            effective_date_gregorian=meta["effective_date_gregorian"],
            expiry_date_gregorian=meta["expiry_date_gregorian"],
            previous_version_document_id=prev_id,
            uploaded_by="system",
            warnings=meta["warnings"]
        )
        db.add(document)
        db.commit()
        db.refresh(document)
        
        # 4. Resolve and create relationships
        for rel in meta["relationships"]:
            target_id = resolve_law_reference(db, rel["target_law_ref"])
            if target_id and target_id != document.id:
                # Create relationship record
                relationship = DocumentRelationship(
                    source_document_id=document.id,
                    target_document_id=target_id,
                    relation_type=rel["relation_type"],
                    status="Confirmed" if rel["confidence"] > 0.90 else "Suggested",
                    extracted_by="LLM" if "LLM" in meta["warnings"] else "regex",
                    extraction_confidence=rel["confidence"]
                )
                db.add(relationship)
                
        db.commit()
        
        # Aggressive memory cleanup after Docling (PyTorch based)
        import torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            
        # Push chunks to Qdrant using the unified v1.1 Ingestion Pipeline
        from document_processor.pipeline import process_and_index_document
        
        indexed_count, quarantined_count = process_and_index_document(
            file_path=local_file_path,
            document_id=document.id,
            tenant_id=job.tenant_id,
            thread_id=job.thread_id,
            s3_key=s3_key,
            family_domain=family.domain,
            lifecycle_status=document.lifecycle_status,
            effective_date_gregorian=document.effective_date_gregorian.isoformat() if document.effective_date_gregorian else None,
            expiry_date_gregorian=document.expiry_date_gregorian.isoformat() if document.expiry_date_gregorian else None,
            allowed_roles=document.allowed_roles or []
        )
        logger.info(f"Background task finished indexing: {indexed_count} v1.1 chunks indexed ({quarantined_count} quarantined).")
        
        job.status = "COMPLETED"
        db.commit()
        
        # Bump the tenant_version in Redis to naturally invalidate cache
        import redis
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        try:
            r = redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
            r.incr(f"tenant_version:{job.tenant_id}")
        except Exception as cache_err:
            logger.warning(f"Failed to bump tenant_version for {job.tenant_id}: {cache_err}")
            
        return True
        
    except Exception as e:
        logger.error(f"Task failed for job {job_id}: {str(e)}")
        job.status = "FAILED"
        job.error_message = str(e)
        db.commit()
        
        # Retry with exponential backoff if it's a transient issue
        raise self.retry(exc=e, countdown=2 ** self.request.retries)
        
    finally:
        db.close()
        # Cleanup temp file
        if local_file_path.exists():
            try:
                os.remove(local_file_path)
            except Exception as e:
                logger.warning(f"Failed to cleanup temp file {local_file_path}: {e}")
