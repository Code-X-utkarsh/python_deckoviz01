from fastapi import APIRouter, HTTPException, Depends
from typing import List, Dict, Any
import requests
import os
import sys

# Add project root to path to access Django models
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, project_root)

router = APIRouter(
    prefix="/curations",
    tags=["curations"]
)

# Django API base URL (for Docker environment)


@router.get("/all")
async def get_all_curations():
    """
    Get all curations (both images and collections) from Django API
    """
    try:
        response = requests.get(f"{DJANGO_API_BASE}/curations/all/", timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch curations: {str(e)}")


@router.get("/images")
async def get_curated_images():
    """
    Get curated images from Django API
    """
    try:
        response = requests.get(f"{DJANGO_API_BASE}/curations/images/", timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch curated images: {str(e)}")


@router.get("/collections")
async def get_curated_collections():
    """
    Get curated collections from Django API
    """
    try:
        response = requests.get(f"{DJANGO_API_BASE}/curations/collections/", timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch curated collections: {str(e)}")
