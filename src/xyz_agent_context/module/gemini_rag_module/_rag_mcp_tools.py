"""
@file_name: _rag_mcp_tools.py
@author: Bin Liang
@date: 2026-03-06
@description: GeminiRAGModule MCP Server tool definitions

Separates MCP tool registration logic from GeminiRAGModule main class,
keeping the module focused on Hook lifecycle and core RAG operations.

Tools:
- rag_query: Query documents in the RAG store
- rag_upload_file: Upload files to the RAG store
- rag_upload_text: Upload text content to the RAG store
"""

import os
import asyncio

from loguru import logger
from mcp.server.fastmcp import FastMCP

from xyz_agent_context.repository import RAGStoreRepository


def create_rag_mcp_server(port: int, module_cls) -> FastMCP:
    """
    Create a GeminiRAGModule MCP Server instance

    Args:
        port: MCP Server port
        module_cls: GeminiRAGModule class (for accessing static methods)

    Returns:
        FastMCP instance with all tools configured
    """
    mcp = FastMCP("gemini_rag_module")
    mcp.settings.port = port

    # -----------------------------------------------------------------
    # Tool: rag_query - Document query tool
    # -----------------------------------------------------------------
    @mcp.tool()
    def rag_query(
        agent_id: str,
        query: str,
        top_k: int = 5
    ) -> dict:
        """
        Search documents in the RAG store using natural language.

        Use this tool to find information from previously uploaded documents.
        The search uses semantic similarity to find relevant content.
        The RAG store is shared across all users for this Agent.

        Args:
            agent_id: The Agent ID (required for store identification)
            query: Natural language search query.
            top_k: Maximum number of results to return (default: 5)

        Returns:
            dict with search results containing text chunks and their sources

        Note:
            - The query should be optimized by combining the user's question, the knowledge base keywords and the keywords likely to appear in the answer.
            - The content of the query will directly affect the accuracy and relevance of the results.
        Example:
            rag_query(
                agent_id="agent_xxx",
                query="What is the main topic of the document?",
                top_k=3
            )
        """
        try:
            chunks = module_cls.query_store(
                agent_id=agent_id,
                query=query,
                top_k=top_k
            )

            if not chunks:
                return {
                    "success": True,
                    "query": query,
                    "total_results": 0,
                    "chunks": [],
                    "message": "No relevant documents found. The store might be empty or no documents match the query."
                }

            return {
                "success": True,
                "query": query,
                "total_results": len(chunks),
                "chunks": chunks,
            }

        except Exception as e:
            logger.error(f"rag_query execution error: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    # -----------------------------------------------------------------
    # Tool: rag_upload_file - File upload tool
    # -----------------------------------------------------------------
    @mcp.tool()
    def rag_upload_file(
        agent_id: str,
        file_path: str,
        update_keywords: bool = True
    ) -> dict:
        """
        Upload a file to the RAG store for future searches.

        The file will be indexed and made available for semantic search.
        Supports various text formats including PDF, TXT, MD, etc.
        The RAG store is shared across all users for this Agent.

        Args:
            agent_id: The Agent ID (required for store identification)
            file_path: Absolute path to the file to upload
            update_keywords: Whether to update keywords using LLM (default: True)

        Returns:
            dict with upload status and details

        Example:
            rag_upload_file(
                agent_id="agent_xxx",
                file_path="/path/to/document.pdf"
            )
        """
        try:
            result = module_cls.upload_file_to_store(
                agent_id=agent_id,
                file_path=file_path
            )

            # After successful upload, update database records and keywords
            if result.get("success") and update_keywords:
                _schedule_keyword_update(
                    module_cls=module_cls,
                    agent_id=agent_id,
                    filename=os.path.basename(file_path),
                    file_content=_read_file_content(file_path),
                    store_name=result.get("store_name", ""),
                    result=result,
                )

            return result

        except FileNotFoundError as e:
            return {
                "success": False,
                "error": str(e)
            }
        except Exception as e:
            logger.error(f"rag_upload_file execution error: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    # -----------------------------------------------------------------
    # Tool: rag_upload_text - Text upload tool
    # -----------------------------------------------------------------
    @mcp.tool()
    def rag_upload_text(
        agent_id: str,
        content: str,
        update_keywords: bool = True
    ) -> dict:
        """
        Upload text content directly to the RAG store.

        Use this to store important information, notes, or any text content
        that should be searchable later. The content will be saved as a
        markdown file and indexed for semantic search.
        The RAG store is shared across all users for this Agent.

        Args:
            agent_id: The Agent ID (required for store identification)
            content: The text content to upload (will be saved as .md file)
            update_keywords: Whether to update keywords using LLM (default: True)

        Returns:
            dict with upload status and details

        Example:
            rag_upload_text(
                agent_id="agent_xxx",
                content="# Important Notes\\n\\nThis is some important information..."
            )
        """
        try:
            if not content or not content.strip():
                return {
                    "success": False,
                    "error": "Content cannot be empty"
                }

            result = module_cls.upload_text_to_store(
                agent_id=agent_id,
                content=content
            )

            # After successful upload, update database records and keywords
            if result.get("success") and update_keywords:
                temp_filename = result.get("temp_filename", "text_upload.md")
                _schedule_keyword_update(
                    module_cls=module_cls,
                    agent_id=agent_id,
                    filename=temp_filename,
                    file_content=content,
                    store_name=result.get("store_name", ""),
                    result=result,
                )

            return result

        except Exception as e:
            logger.error(f"rag_upload_text execution error: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    return mcp


# =========================================================================
# Internal helpers
# =========================================================================

def _read_file_content(file_path: str) -> str:
    """Read file content for keyword extraction (truncated to 3000 chars)."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()[:3000]
    except Exception:
        return f"[Binary file: {os.path.basename(file_path)}]"


def _schedule_keyword_update(
    module_cls,
    agent_id: str,
    filename: str,
    file_content: str,
    store_name: str,
    result: dict,
) -> None:
    """
    Schedule async database record creation and keyword update.

    Handles event loop detection: if a loop is already running,
    schedules as a background task; otherwise runs synchronously.
    """
    try:
        async def update_db_and_keywords():
            from xyz_agent_context.utils import get_db_client
            db = await get_db_client()
            repo = RAGStoreRepository(db)

            # Ensure database record exists (Agent level)
            await repo.get_or_create_store(
                agent_id=agent_id,
                user_id=None,
                store_name=store_name,
            )

            # Add file record
            await repo.add_uploaded_file(
                agent_id=agent_id,
                user_id=None,
                filename=filename,
            )
            logger.info(f"Added file record: {filename}")

            # Update keywords using LLM
            current_keywords, new_keywords = await module_cls.update_keywords_with_llm(
                agent_id=agent_id,
                new_file_content=file_content,
                new_filename=filename,
                db_client=db,
            )
            logger.info(f"Original keywords: {current_keywords}")
            logger.info(f"New keywords: {new_keywords}")
            return new_keywords

        # Execute async operations in event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(update_db_and_keywords())
                result["keywords_update"] = "scheduled"
            else:
                new_keywords = loop.run_until_complete(update_db_and_keywords())
                result["keywords"] = new_keywords
        except RuntimeError:
            new_keywords = asyncio.run(update_db_and_keywords())
            result["keywords"] = new_keywords

    except Exception as e:
        logger.warning(f"Failed to update keywords: {e}")
        result["keywords_update_error"] = str(e)
