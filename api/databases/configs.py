# database.py
from dotenv import load_dotenv
import os
from redis import Redis

# Load environment variables
load_dotenv()

# PostgreSQL Database connection
DATABASE_URL = (
)


# Redis connection
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

# PostgreSQL Database dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Raw PostgreSQL client connection if needed
def get_client():
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
