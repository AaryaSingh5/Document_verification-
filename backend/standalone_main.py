"""Standalone FastAPI server for Suraksha Setu Document Verification Module.

Allows running and testing the OCR document verification module in isolation
without requiring the parent Suraksha Setu backend.

Run with:
    uvicorn standalone_main:app --reload --port 8000
or:
    python standalone_main.py
"""

import logging
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from document_verification.config import (
    ALLOWED_EXTENSIONS,
    ALLOWED_MIME_TYPES,
    MAX_UPLOAD_SIZE_BYTES,
    MIN_CONFIDENCE_FOR_AUTO_VERIFY,
    MIN_CONFIDENCE_FOR_REVIEW,
    OCR_MODE,
)
from document_verification.router import router as verification_router
from document_verification.face_match_router import router as face_match_router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("suraksha_setu.document_verification")

app = FastAPI(
    title="Suraksha Setu - Document Verification API",
    description=(
        "Standalone OCR-based Identity Document Verification Service for "
        "the Suraksha Setu Smart Tourist Safety Platform."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS middleware for development with React/Vite frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount document verification router
app.include_router(verification_router, prefix="/api/v1/verifications")
app.include_router(face_match_router, prefix="/api/v1/verifications")



@app.get("/health", tags=["Health"])
def health_check():
    """Service health and diagnostic status."""
    return {
        "status": "healthy",
        "service": "Suraksha Setu - Document Verification Module",
        "version": "1.0.0",
        "ocr_mode": OCR_MODE,
        "config": {
            "min_confidence_auto_verify": MIN_CONFIDENCE_FOR_AUTO_VERIFY,
            "min_confidence_review": MIN_CONFIDENCE_FOR_REVIEW,
            "max_upload_size_mb": MAX_UPLOAD_SIZE_BYTES / (1024 * 1024),
            "allowed_extensions": list(ALLOWED_EXTENSIONS),
            "allowed_mime_types": list(ALLOWED_MIME_TYPES),
        },
    }


@app.get("/", tags=["Info"])
def root_info():
    """Root info page."""
    return {
        "name": "Suraksha Setu Identity Document Verification API",
        "documentation": "/docs",
        "health": "/health",
        "endpoints": {
            "upload": "POST /api/v1/verifications/upload",
            "confirm": "POST /api/v1/verifications/confirm",
            "status": "GET /api/v1/verifications/{verification_id}",
            "delete": "DELETE /api/v1/verifications/{verification_id}",
        },
    }


if __name__ == "__main__":
    logger.info("Starting standalone Suraksha Setu Verification server on http://localhost:8000")
    uvicorn.run(
        "standalone_main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
