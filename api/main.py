# app.py
from fastapi import FastAPI, Depends
import logging
from fastapi.middleware.cors import CORSMiddleware
import os
import sys

# Ensure debug logs for all modules (including WebSocket router)
logging.basicConfig(level=logging.DEBUG)

# Ensure project root (parent of api) is on PYTHONPATH so deckoviz_ai is importable
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from databases.configs import get_redis_client
from middleware.logging import RequestLoggingMiddleware

# Initialize the application
app = FastAPI()

# Add request logging middleware (should be first)
app.add_middleware(RequestLoggingMiddleware)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust this to restrict origins if needed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers 
app.include_router(websocket.router)
app.include_router(rooms.router)
app.include_router(qr_code_redis.router)
app.include_router(curations.router)


@app.get("/", dependencies=[Depends(get_redis_client)])
async def root():
    return {"message": "Welcome to the Deckoviz API."}
