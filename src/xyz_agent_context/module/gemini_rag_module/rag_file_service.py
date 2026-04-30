"""
RAG File Service - RAG file management service layer

@file_name: rag_file_service.py
@author: NetMind.AI
@date: 2025-12-02
@description: Business logic service for RAG file upload and status management

=============================================================================
Module Overview
=============================================================================

Extracts RAG file management business logic from the API layer into this service layer,
following these principles:
1. API layer only handles HTTP request/response processing
2. Business logic is encapsulated in the service layer
3. Service layer can be called from multiple entry points (API, CLI, other modules)

Core features:
1. File path management - Temporary directory and status file paths
2. Status tracking - Read/write upload status (pending/uploading/completed/failed)
3. Background upload - Async upload to Gemini with keyword updates

Architecture:
    +-------------------------------------------------------------+
    |                     API Layer (agents.py)                     |
    |                  Only handles HTTP requests/responses         |
    +----------------------------+--------------------------------+
                                 | calls
    +----------------------------v--------------------------------+
    |                   RAGFileService                             |
    |         Business logic: path management, status tracking,    |
    |         background upload                                    |
    +----------------------------+--------------------------------+
                                 | calls
    +----------------------------v--------------------------------+
    |            GeminiRAGTrigger + GeminiRAGModule                |
    |                    Actual Gemini API operations              |
    +-------------------------------------------------------------+
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from xyz_agent_context.utils.file_safety import ensure_within_directory, sanitize_filename


def convert_document_to_markdown(file_path: str) -> str:
    """
    Convert a document to Markdown format using docling

    Supports multiple document formats: PDF, DOCX, PPTX, images, HTML, etc.

    Args:
        file_path: Path to the document file (absolute or relative)

    Returns:
        str: Converted Markdown text content

    Raises:
        FileNotFoundError: If the file does not exist
        ImportError: If the docling module is not installed
        Exception: If document parsing fails

    Example:
        >>> markdown_text = convert_document_to_markdown("/path/to/document.pdf")
        >>> print(markdown_text[:500])  # Print first 500 characters
    """
    from pathlib import Path
    from docling.document_converter import DocumentConverter


    # Check if file exists
    file_path_obj = Path(file_path)
    if not file_path_obj.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    # Create converter
    converter = DocumentConverter()

    # Execute conversion
    result = converter.convert(str(file_path))

    # Get Markdown text
    markdown_text = result.document.export_to_markdown()

    return markdown_text


class RAGFileService:
    """
    RAG File Management Service

    Provides static methods for managing RAG file temporary storage and upload status.
    """

    # Base path configuration — under ~/.nexusagent so it's independent of cwd
    BASE_PATH = Path.home() / ".nexusagent" / "data" / "gemini_rag_temp"

    # =========================================================================
    # Path Management
    # =========================================================================

    @staticmethod
    def get_temp_path(agent_id: str, user_id: str) -> Path:
        """
        Get the RAG temporary file directory path

        Args:
            agent_id: Agent ID
            user_id: User ID

        Returns:
            Path: Format is ~/.nexusagent/data/gemini_rag_temp/agent_{agent_id}_user_{user_id}
        """
        return RAGFileService.BASE_PATH / f"agent_{agent_id}_user_{user_id}"

    @staticmethod
    def get_status_file_path(agent_id: str, user_id: str) -> Path:
        """
        Get the status JSON file path

        Args:
            agent_id: Agent ID
            user_id: User ID

        Returns:
            Path: Status file path
        """
        return RAGFileService.get_temp_path(agent_id, user_id) / "_rag_status.json"

    # =========================================================================
    # Status Management
    # =========================================================================

    @staticmethod
    def load_status(agent_id: str, user_id: str) -> Dict[str, Any]:
        """
        Load RAG status

        Args:
            agent_id: Agent ID
            user_id: User ID

        Returns:
            Status dictionary, format: {"files": {"filename": {"status": "...", ...}}}
        """
        status_file = RAGFileService.get_status_file_path(agent_id, user_id)
        if status_file.exists():
            try:
                with open(status_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load RAG status: {e}")
        return {"files": {}}

    @staticmethod
    def save_status(agent_id: str, user_id: str, status: Dict[str, Any]) -> None:
        """
        Save RAG status

        Args:
            agent_id: Agent ID
            user_id: User ID
            status: Status dictionary
        """
        status_file = RAGFileService.get_status_file_path(agent_id, user_id)
        status_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(status_file, "w", encoding="utf-8") as f:
                json.dump(status, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.exception(f"Failed to save RAG status: {e}")

    @staticmethod
    def update_file_status(
        agent_id: str,
        user_id: str,
        filename: str,
        status: str,
        error: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Update the status of a single file

        Args:
            agent_id: Agent ID
            user_id: User ID
            filename: Filename
            status: Status (pending/uploading/completed/failed)
            error: Error message (optional)
            extra: Extra information (optional)
        """
        status_data = RAGFileService.load_status(agent_id, user_id)
        if "files" not in status_data:
            status_data["files"] = {}

        file_status = {
            "status": status,
            "updated_at": datetime.now().isoformat(),
        }
        if error:
            file_status["error"] = error
        if extra:
            file_status.update(extra)

        status_data["files"][filename] = file_status
        RAGFileService.save_status(agent_id, user_id, status_data)

    @staticmethod
    def remove_file_status(agent_id: str, user_id: str, filename: str) -> None:
        """
        Remove a file status record

        Args:
            agent_id: Agent ID
            user_id: User ID
            filename: Filename
        """
        status_data = RAGFileService.load_status(agent_id, user_id)
        if "files" in status_data and filename in status_data["files"]:
            del status_data["files"][filename]
            RAGFileService.save_status(agent_id, user_id, status_data)

    # =========================================================================
    # File Operations
    # =========================================================================

    @staticmethod
    def list_files(agent_id: str, user_id: str) -> List[Dict[str, Any]]:
        """
        List all RAG files and their status

        Args:
            agent_id: Agent ID
            user_id: User ID

        Returns:
            File list, each element contains:
            - filename: Filename
            - size: File size
            - modified_at: Modification time
            - upload_status: Upload status
            - error_message: Error message (optional)
        """
        rag_path = RAGFileService.get_temp_path(agent_id, user_id)
        status_data = RAGFileService.load_status(agent_id, user_id)
        file_statuses = status_data.get("files", {})

        files = []
        if rag_path.exists():
            for filepath in rag_path.iterdir():
                # Skip status files
                if filepath.name.startswith("_"):
                    continue
                if filepath.is_file():
                    stat = filepath.stat()
                    file_status = file_statuses.get(filepath.name, {})

                    files.append({
                        "filename": filepath.name,
                        "size": stat.st_size,
                        "modified_at": str(stat.st_mtime),
                        "upload_status": file_status.get("status", "pending"),
                        "error_message": file_status.get("error"),
                    })

        # Sort by modification time descending
        files.sort(key=lambda f: f["modified_at"], reverse=True)
        return files

    @staticmethod
    def save_file(
        agent_id: str,
        user_id: str,
        filename: str,
        content: bytes
    ) -> Path:
        """
        Save file to temporary directory

        Args:
            agent_id: Agent ID
            user_id: User ID
            filename: Filename
            content: File content

        Returns:
            Path of the saved file
        """
        rag_path = RAGFileService.get_temp_path(agent_id, user_id)
        rag_path.mkdir(parents=True, exist_ok=True)

        safe_filename = sanitize_filename(filename, label="filename")
        filepath = ensure_within_directory(rag_path, safe_filename, label="filename")
        with open(filepath, "wb") as f:
            f.write(content)

        logger.info(f"RAG file saved: {filepath} ({len(content)} bytes)")
        return filepath

    @staticmethod
    def delete_file(agent_id: str, user_id: str, filename: str) -> bool:
        """
        Delete a file

        Args:
            agent_id: Agent ID
            user_id: User ID
            filename: Filename

        Returns:
            Whether deletion was successful
        """
        rag_path = RAGFileService.get_temp_path(agent_id, user_id)
        safe_filename = sanitize_filename(filename, label="filename")
        filepath = ensure_within_directory(rag_path, safe_filename, label="filename")

        if not filepath.exists():
            return False

        filepath.unlink()
        RAGFileService.remove_file_status(agent_id, user_id, safe_filename)
        logger.info(f"RAG file deleted: {filepath}")
        return True

    # =========================================================================
    # Background Upload
    # =========================================================================

    @staticmethod
    async def upload_to_gemini_store(
        agent_id: str,
        user_id: str,
        file_path: str,
        filename: str
    ) -> None:
        """
        Background task: Upload file to Gemini store and update keywords

        This function is designed to run in the background without blocking the main request.

        Args:
            agent_id: Agent ID
            user_id: User ID
            file_path: Full file path
            filename: Filename
        """
        try:
            # Update status to "uploading"
            RAGFileService.update_file_status(
                agent_id, user_id, filename, "uploading",
                extra={"started_at": datetime.now().isoformat()}
            )

            # Call GeminiRAGTrigger to upload
            from xyz_agent_context.module.gemini_rag_module.gemini_rag_trigger import GeminiRAGTrigger

            result = GeminiRAGTrigger.upload_file(
                agent_id=agent_id,
                user_id=user_id,
                file_path=file_path,
                wait_seconds=5
            )

            if result.get("success"):
                # Upload successful, update database and keywords
                try:
                    await RAGFileService._update_db_and_keywords(
                        agent_id=agent_id,
                        user_id=user_id,
                        file_path=file_path,
                        filename=filename,
                        store_name=result.get("store_name", ""),
                    )
                except Exception as e:
                    logger.warning(f"Failed to update keywords for {filename}: {e}")

                # Update status to "completed"
                RAGFileService.update_file_status(
                    agent_id, user_id, filename, "completed",
                    extra={"completed_at": datetime.now().isoformat()}
                )
                logger.info(f"RAG file uploaded successfully: {filename}")

            else:
                # Upload failed
                RAGFileService.update_file_status(
                    agent_id, user_id, filename, "failed",
                    error=result.get("error", "Unknown error")
                )
                logger.error(f"RAG file upload failed: {filename}, error: {result.get('error')}")

        except Exception as e:
            logger.exception(f"Error in background upload task: {e}")
            RAGFileService.update_file_status(
                agent_id, user_id, filename, "failed",
                error=str(e)
            )

    @staticmethod
    async def _update_db_and_keywords(
        agent_id: str,
        user_id: str,
        file_path: str,
        filename: str,
        store_name: str,
    ) -> None:
        """
        Update database records and keywords

        Args:
            agent_id: Agent ID
            user_id: User ID
            file_path: File path
            filename: Filename
            store_name: Gemini store name
        """
        from xyz_agent_context.repository import RAGStoreRepository
        from xyz_agent_context.utils import get_db_client
        from xyz_agent_context.module.gemini_rag_module import GeminiRAGModule


        # Get database connection and repository
        db = await get_db_client()
        repo = RAGStoreRepository(db)

        # Ensure database record exists
        await repo.get_or_create_store(
            agent_id=agent_id,
            user_id=user_id,
            store_name=store_name
        )

        # Add file record
        await repo.add_uploaded_file(
            agent_id=agent_id,
            user_id=user_id,
            filename=filename
        )

        # Update keywords using LLM
        current_keywords, new_keywords = await GeminiRAGModule.update_keywords_with_llm(
            agent_id=agent_id,
            user_id=user_id,
            file_path=file_path,
            db_client=db
        )

        logger.info(f"RAG file uploaded and keywords updated: {filename}")

    # =========================================================================
    # Statistics
    # =========================================================================

    @staticmethod
    def get_stats(agent_id: str, user_id: str) -> Dict[str, int]:
        """
        Get file statistics

        Args:
            agent_id: Agent ID
            user_id: User ID

        Returns:
            Statistics: total_count, completed_count, pending_count, failed_count
        """
        files = RAGFileService.list_files(agent_id, user_id)

        completed = sum(1 for f in files if f["upload_status"] == "completed")
        pending = sum(1 for f in files if f["upload_status"] in ("pending", "uploading"))
        failed = sum(1 for f in files if f["upload_status"] == "failed")

        return {
            "total_count": len(files),
            "completed_count": completed,
            "pending_count": pending,
            "failed_count": failed,
        }
