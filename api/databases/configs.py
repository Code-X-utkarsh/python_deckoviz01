# database.py
from dotenv import load_dotenv
import os
import logging
from redis import Redis

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

# PostgreSQL Database connection
DATABASE_URL = (
    f"postgresql://{os.getenv('DB_USER', 'postgres')}:{os.getenv('DB_PASSWORD', 'postgres')}"
    f"@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}/{os.getenv('DB_NAME', 'deckoviz')}"
)

# SQLAlchemy setup — lazy initialization to avoid crash when DB is unreachable
_engine = None
_SessionLocal = None
_Base = None


def _init_db():
    """Lazily initialise SQLAlchemy engine, session factory and Base."""
    global _engine, _SessionLocal, _Base
    if _engine is None:
        from sqlalchemy import create_engine
        from sqlalchemy.ext.declarative import declarative_base
        from sqlalchemy.orm import sessionmaker
        _engine = create_engine(DATABASE_URL)
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
        _Base = declarative_base()
    return _engine, _SessionLocal, _Base


# Redis connection
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)


# PostgreSQL Database dependency
def get_db():
    _, SessionLocal, _ = _init_db()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Raw PostgreSQL client connection if needed
def get_client():
    engine, _, _ = _init_db()
    return engine.connect()


# Redis client dependency
def get_redis_client():
    """
    Redis client dependency. Returns a synchronous Redis client.
    """
    client = Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=False,
    )
    return client
