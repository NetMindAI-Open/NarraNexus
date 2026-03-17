"""
@file_name: agents_rag.py
@author: NetMind.AI
@date: 2025-11-28
@description: Agent RAG file management routes

Provides endpoints for:
- GET /{agent_id}/rag-files - List RAG files and their status
- POST /{agent_id}/rag-files - Upload file to RAG store
- DELETE /{agent_id}/rag-files/{filename} - Delete RAG file
"""

import asyncio

from fastapi import APIRouter, Query, UploadFile, File
from loguru import logger

from backend.config import settings as backend_settings
from xyz_agent_context.module.gemini_rag_module.rag_file_service import RAGFileService
from xyz_agent_context.schema import (
    RAGFileInfo,
    RAGFileListResponse,
    RAGFileUploadResponse,
    RAGFileDeleteResponse,
)
from xyz_agent_context.utils.file_safety import enforce_max_bytes, sanitize_filename


router = APIRouter()


@router.get("/{agent_id}/rag-files", response_model=RAGFileListResponse)
async def list_rag_files(
    agent_id: str,
    user_id: str = Query(..., description="User ID"),
):
    """List all RAG files and upload status for Agent-User pair"""
    logger.info(f"Listing RAG files for agent: {agent_id}, user: {user_id}")

    try:
        files_data = RAGFileService.list_files(agent_id, user_id)
        stats = RAGFileService.get_stats(agent_id, user_id)

        files = [RAGFileInfo(**f) for f in files_data]

        return RAGFileListResponse(
            success=True,
            files=files,
            total_count=stats["total_count"],
            completed_count=stats["completed_count"],
            pending_count=stats["pending_count"],
        )

    except Exception as e:
        logger.error(f"Error listing RAG files: {e}")
        return RAGFileListResponse(success=False, error=str(e))


@router.post("/{agent_id}/rag-files", response_model=RAGFileUploadResponse)
async def upload_rag_file(
    agent_id: str,
    user_id: str = Query(..., description="User ID"),
    file: UploadFile = File(..., description="File to upload to RAG store"),
):
    """Upload file to RAG temp directory and trigger Gemini store upload"""
    # Supported file formats
    supported_extensions = {".txt", ".md", ".pdf"}

    try:
        safe_filename = sanitize_filename(
            file.filename or "",
            label="filename",
            allowed_extensions=supported_extensions,
        )
        logger.info(f"Uploading RAG file '{safe_filename}' for agent: {agent_id}, user: {user_id}")

        content = await file.read()
        file_size = len(content)
        enforce_max_bytes(file_size, backend_settings.max_upload_bytes, label="RAG file")

        filepath = RAGFileService.save_file(agent_id, user_id, safe_filename, content)

        RAGFileService.update_file_status(
            agent_id, user_id, safe_filename, "pending",
            extra={"saved_at": str(filepath.stat().st_mtime)}
        )

        # Trigger background upload task
        asyncio.create_task(
            RAGFileService.upload_to_gemini_store(agent_id, user_id, str(filepath), safe_filename)
        )

        return RAGFileUploadResponse(
            success=True,
            filename=safe_filename,
            size=file_size,
            upload_status="pending",
        )

    except ValueError as e:
        logger.warning(f"Rejected RAG upload for agent={agent_id}, user={user_id}: {e}")
        return RAGFileUploadResponse(success=False, error=str(e))
    except Exception as e:
        logger.error(f"Error uploading RAG file: {e}")
        return RAGFileUploadResponse(success=False, error=str(e))


@router.delete("/{agent_id}/rag-files/{filename}", response_model=RAGFileDeleteResponse)
async def delete_rag_file(
    agent_id: str,
    filename: str,
    user_id: str = Query(..., description="User ID"),
):
    """
    Delete file from RAG temp directory

    Note: Does not delete from Gemini store (Gemini does not support deletion).
    """
    logger.info(f"Deleting RAG file '{filename}' for agent: {agent_id}, user: {user_id}")

    try:
        safe_filename = sanitize_filename(filename, label="filename")
        deleted = RAGFileService.delete_file(agent_id, user_id, safe_filename)

        if not deleted:
            return RAGFileDeleteResponse(
                success=False,
                error=f"File not found: {safe_filename}"
            )

        return RAGFileDeleteResponse(success=True, filename=safe_filename)

    except ValueError as e:
        return RAGFileDeleteResponse(success=False, error=str(e))
    except Exception as e:
        logger.error(f"Error deleting RAG file: {e}")
        return RAGFileDeleteResponse(success=False, error=str(e))
