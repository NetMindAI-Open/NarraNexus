"""
@file_name: agents_files.py
@author: NetMind.AI
@date: 2025-11-28
@description: Agent workspace file management routes

Provides endpoints for:
- GET /{agent_id}/files - List workspace files
- POST /{agent_id}/files - Upload file to workspace
- DELETE /{agent_id}/files/{filename} - Delete workspace file
"""

import os
from pathlib import Path

from fastapi import APIRouter, Query, UploadFile, File
from loguru import logger

from backend.config import settings as backend_settings
from xyz_agent_context.schema import (
    FileInfo,
    FileListResponse,
    FileUploadResponse,
    FileDeleteResponse,
)
from xyz_agent_context.utils.file_safety import (
    enforce_max_bytes,
    ensure_within_directory,
    sanitize_filename,
)


router = APIRouter()


def _get_workspace_path(agent_id: str, user_id: str) -> str:
    """Get Agent-User workspace path"""
    from xyz_agent_context.settings import settings
    base_path = settings.base_working_path
    return os.path.join(base_path, f"{agent_id}_{user_id}")


@router.get("/{agent_id}/files", response_model=FileListResponse)
async def list_workspace_files(
    agent_id: str,
    user_id: str = Query(..., description="User ID")
):
    """List all files in Agent workspace"""
    logger.info(f"Listing files for agent: {agent_id}, user: {user_id}")

    try:
        workspace_path = _get_workspace_path(agent_id, user_id)

        if not os.path.exists(workspace_path):
            return FileListResponse(success=True, files=[], workspace_path=workspace_path)

        files = []
        for filename in os.listdir(workspace_path):
            filepath = os.path.join(workspace_path, filename)
            if os.path.isfile(filepath):
                stat = os.stat(filepath)
                files.append(FileInfo(
                    filename=filename,
                    size=stat.st_size,
                    modified_at=str(stat.st_mtime),
                ))

        files.sort(key=lambda f: f.modified_at, reverse=True)

        return FileListResponse(
            success=True, files=files, workspace_path=workspace_path
        )

    except Exception as e:
        logger.error(f"Error listing files: {e}")
        return FileListResponse(success=False, error=str(e))


@router.post("/{agent_id}/files", response_model=FileUploadResponse)
async def upload_file(
    agent_id: str,
    user_id: str = Query(..., description="User ID"),
    file: UploadFile = File(..., description="File to upload"),
):
    """Upload file to Agent workspace"""
    logger.info(f"Uploading file '{file.filename}' for agent: {agent_id}, user: {user_id}")

    try:
        safe_filename = sanitize_filename(file.filename or "", label="filename")

        workspace_path = _get_workspace_path(agent_id, user_id)
        workspace_dir = Path(workspace_path)

        if not workspace_dir.exists():
            workspace_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created workspace directory: {workspace_path}")

        content = await file.read()
        enforce_max_bytes(len(content), backend_settings.max_upload_bytes, label="File")
        filepath = ensure_within_directory(workspace_dir, safe_filename, label="filename")

        with open(filepath, "wb") as f:
            f.write(content)

        file_size = len(content)
        logger.info(f"File saved: {filepath} ({file_size} bytes)")

        return FileUploadResponse(
            success=True,
            filename=safe_filename,
            size=file_size,
            workspace_path=workspace_path,
        )

    except ValueError as e:
        return FileUploadResponse(success=False, error=str(e))
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        return FileUploadResponse(success=False, error=str(e))


@router.delete("/{agent_id}/files/{filename}", response_model=FileDeleteResponse)
async def delete_file(
    agent_id: str,
    filename: str,
    user_id: str = Query(..., description="User ID"),
):
    """Delete file from Agent workspace"""
    logger.info(f"Deleting file '{filename}' for agent: {agent_id}, user: {user_id}")

    try:
        safe_filename = sanitize_filename(filename, label="filename")
        workspace_path = _get_workspace_path(agent_id, user_id)
        filepath = ensure_within_directory(Path(workspace_path), safe_filename, label="filename")

        if not os.path.exists(filepath):
            return FileDeleteResponse(
                success=False,
                error=f"File not found: {safe_filename}"
            )

        os.remove(filepath)
        logger.info(f"File deleted: {filepath}")

        return FileDeleteResponse(success=True, filename=safe_filename)

    except ValueError as e:
        return FileDeleteResponse(success=False, error=str(e))
    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        return FileDeleteResponse(success=False, error=str(e))
