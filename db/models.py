import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Text, ForeignKey, JSON, Integer, Float, Date, event
from sqlalchemy.orm import relationship
from db.database import Base, SessionLocal

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    threads = relationship("ChatThread", back_populates="user", cascade="all, delete-orphan")
    documents = relationship("UploadedDocument", back_populates="user", cascade="all, delete-orphan")

class UploadedDocument(Base):
    __tablename__ = "uploaded_documents"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    filename = Column(String, nullable=False)
    s3_key = Column(String, nullable=False, unique=True)
    status = Column(String, default="PENDING")
    created_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship("User", back_populates="documents")

class ChatThread(Base):
    __tablename__ = "chat_threads"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String, default="New Chat")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = relationship("User", back_populates="threads")
    messages = relationship("ChatMessage", back_populates="thread", cascade="all, delete-orphan", order_by="ChatMessage.created_at")

class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    thread_id = Column(String, ForeignKey("chat_threads.id"), nullable=False, index=True)
    role = Column(String, nullable=False) # 'user' or 'assistant'
    content = Column(Text, nullable=False)
    metadata_json = Column(JSON, nullable=True) # To store verification report, sources, etc.
    created_at = Column(DateTime, default=datetime.utcnow)
    
    thread = relationship("ChatThread", back_populates="messages")

class DocumentJob(Base):
    __tablename__ = "document_jobs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, index=True, nullable=False, default="default_tenant")
    thread_id = Column(String, index=True, nullable=True)
    s3_key = Column(String, nullable=False)
    status = Column(String, default="PENDING") # PENDING, PROCESSING, COMPLETED, FAILED
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "thread_id": self.thread_id,
            "s3_key": self.s3_key,
            "status": self.status,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }

class DocumentFamily(Base):
    __tablename__ = "document_families"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, index=True, nullable=False, default="default_tenant")
    title = Column(String, nullable=False)
    domain = Column(String, index=True, nullable=False) # e.g. 'HR', 'Finance'
    created_at = Column(DateTime, default=datetime.utcnow)
    
    documents = relationship("Document", back_populates="document_family", cascade="all, delete-orphan")

class Document(Base):
    __tablename__ = "documents"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_family_id = Column(String, ForeignKey("document_families.id"), nullable=False, index=True)
    version = Column(String, nullable=False, default="1.0")
    lifecycle_status = Column(String, nullable=False, default="Active") # Enum: Draft, Pending_Review, Active, Archived, Superseded, Rejected
    allowed_roles = Column(JSON, nullable=True) # List of roles e.g. ["HR_Manager"]
    applicability = Column(JSON, nullable=True) # Key-value metadata e.g. {"country": "UAE"}
    original_calendar = Column(String, nullable=True) # 'Hijri' or 'Gregorian'
    original_effective_date = Column(String, nullable=True)
    effective_date_gregorian = Column(Date, index=True, nullable=True)
    expiry_date_gregorian = Column(Date, index=True, nullable=True)
    previous_version_document_id = Column(String, ForeignKey("documents.id"), nullable=True)
    uploaded_by = Column(String, nullable=False, default="system")
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    approved_by = Column(String, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    warnings = Column(JSON, nullable=True)
    
    document_family = relationship("DocumentFamily", back_populates="documents")
    previous_version = relationship("Document", remote_side=[id], backref="next_versions")

@event.listens_for(Document, 'before_insert')
@event.listens_for(Document, 'before_update')
def handle_document_family_versioning(mapper, connection, target):
    # Set status of previous versions of document family to Superseded
    if target.lifecycle_status == "Active":
        # Delete old vectors from Qdrant
        from db.qdrant_client import qdrant_manager
        from qdrant_client import models as qdrant_models
        
        db = SessionLocal()
        old_docs = db.query(Document).filter(
            Document.document_family_id == target.document_family_id,
            Document.lifecycle_status == "Active",
            Document.id != target.id
        ).all()
        for doc in old_docs:
            doc.lifecycle_status = "Superseded"
            try:
                points_data = qdrant_manager.client.scroll(
                    collection_name=qdrant_manager.collection_name,
                    scroll_filter=qdrant_models.Filter(
                        must=[
                            qdrant_models.FieldCondition(
                                key="document_id",
                                match=qdrant_models.MatchValue(value=doc.id)
                            )
                        ]
                    ),
                    limit=1000
                )[0]
                point_ids = [p.id for p in points_data]
                if point_ids:
                    qdrant_manager.client.set_payload(
                        collection_name=qdrant_manager.collection_name,
                        payload={"lifecycle_status": "Superseded"},
                        points=point_ids
                    )
            except Exception as e:
                from utils.logging import logger
                logger.warning(f"Failed to update superseded document {doc.id} payload in Qdrant: {e}")
        db.commit()
        db.close()

class DocumentRelationship(Base):
    __tablename__ = "document_relationships"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    source_document_id = Column(String, ForeignKey("documents.id"), nullable=False, index=True)
    target_document_id = Column(String, ForeignKey("documents.id"), nullable=False, index=True)
    relation_type = Column(String, nullable=False) # implements, amends, supersedes, references, corrects
    status = Column(String, nullable=False, default="Suggested") # Suggested, Confirmed, Rejected
    extracted_by = Column(String, nullable=False, default="regex") # regex, LLM, admin
    extraction_confidence = Column(Float, nullable=False, default=1.0)
    created_at = Column(DateTime, default=datetime.utcnow)

class AuthorityRank(Base):
    __tablename__ = "authority_ranks"
    id = Column(Integer, primary_key=True, autoincrement=True)
    jurisdiction = Column(String, nullable=False, index=True) # e.g. UAE, KSA
    document_type = Column(String, nullable=False) # e.g. Federal Law, Cabinet Resolution
    rank = Column(Integer, nullable=False) # higher number = higher authority
    created_at = Column(DateTime, default=datetime.utcnow)

class OrganizationGlossary(Base):
    __tablename__ = "organization_glossaries"
    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String, nullable=False, index=True, default="default_tenant")
    source_term = Column(String, nullable=False) # e.g. CBUAE
    canonical_term = Column(String, nullable=False) # e.g. مصرف الإمارات المركزي
    created_at = Column(DateTime, default=datetime.utcnow)
