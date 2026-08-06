import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv
import logging

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

try:
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL is not configured.")
    # Short timeout to fail fast if server is offline
    if "postgresql" in DATABASE_URL:
        engine = create_engine(DATABASE_URL, echo=False, pool_size=20, max_overflow=10, connect_args={"connect_timeout": 3})
    else:
        engine = create_engine(DATABASE_URL, echo=False)
    with engine.connect() as conn:
        pass
except Exception as e:
    logging.getLogger("Database").warning(f"PostgreSQL connection failed ({e}). Falling back to SQLite local metadata DB.")
    DATABASE_URL = "sqlite:///ragnr_metadata.db"
    engine = create_engine(DATABASE_URL, echo=False)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
