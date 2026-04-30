"""
Gemini RAG Trigger - Static utility class for document upload

@file_name: gemini_rag_trigger.py
@author: NetMind.AI
@date: 2025-12-02
@description: Provides static methods for uploading documents to Gemini File Search store

=============================================================================
Module Overview
=============================================================================

GeminiRAGTrigger provides static utility methods that can be called by other
modules or external code to upload documents to and query the Gemini RAG system.

Unlike JobTrigger (which is a background polling service),
GeminiRAGTrigger is a pure collection of static methods for document management operations.

Core features:
1. Document upload - upload_file, upload_text, upload_files_batch
2. Document query - query
3. Store management - get_store_name, ensure_store_exists, list_stores

Usage examples:
    from xyz_agent_context.module.gemini_rag_module.gemini_rag_trigger import GeminiRAGTrigger

    # Upload file
    result = GeminiRAGTrigger.upload_file(
        agent_id="agent_xxx",
        user_id="user_xxx",
        file_path="/path/to/document.pdf"
    )

    # Upload text content
    result = GeminiRAGTrigger.upload_text(
        agent_id="agent_xxx",
        user_id="user_xxx",
        content="This is important information..."
    )

    # Query documents
    chunks = GeminiRAGTrigger.query(
        agent_id="agent_xxx",
        user_id="user_xxx",
        query="What is the main topic of the document?"
    )
"""

from typing import Dict, Any, List, Optional
from loguru import logger

# Import main module to use its static methods
from xyz_agent_context.module.gemini_rag_module.gemini_rag_module import GeminiRAGModule


class GeminiRAGTrigger:
    """
    Gemini RAG Trigger - Static utility class for document operations

    Provides convenient static methods for:
    - Uploading files to RAG store
    - Uploading text content to RAG store
    - Querying documents from RAG store
    - Managing stores (create, list, etc.)

    All methods are static and can be called without instantiation.

    Use cases:
    - Other modules need to upload documents to the RAG system
    - External scripts need to batch upload files
    - Testing or debugging requires direct RAG system operations
    """

    # =========================================================================
    # Document Upload Methods
    # =========================================================================

    @staticmethod
    def upload_file(
        agent_id: str,
        user_id: str,
        file_path: str,
        wait_seconds: int = 5
    ) -> Dict[str, Any]:
        """
        Upload file to RAG store

        This is the primary method for adding documents to the knowledge base.
        The file will be indexed and searchable via semantic search.

        Args:
            agent_id: Agent ID, used to locate the store
            user_id: User ID, used to locate the store
            file_path: Absolute path of the file
            wait_seconds: Time to wait for indexing completion (seconds), default 5

        Returns:
            Dict[str, Any]: Upload result
            - success: bool, whether successful
            - store_name: str, store name (on success)
            - file_path: str, uploaded file path (on success)
            - message: str, result message
            - error: str, error message (on failure)

        Example:
            result = GeminiRAGTrigger.upload_file(
                agent_id="agent_123",
                user_id="user_456",
                file_path="/data/documents/report.pdf"
            )
            if result["success"]:
                print(f"Uploaded to {result['store_name']}")
        """
        try:
            logger.info(f"[GeminiRAGTrigger] Uploading file: {file_path}")
            result = GeminiRAGModule.upload_file_to_store(
                agent_id=agent_id,
                file_path=file_path,
                wait_seconds=wait_seconds
            )
            logger.info(f"[GeminiRAGTrigger] Upload successful: {file_path}")
            return result

        except FileNotFoundError as e:
            logger.exception(f"[GeminiRAGTrigger] File not found: {file_path}")
            return {
                "success": False,
                "error": str(e)
            }
        except Exception as e:
            logger.exception(f"[GeminiRAGTrigger] Upload failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    @staticmethod
    def upload_text(
        agent_id: str,
        user_id: str,
        content: str,
        wait_seconds: int = 5
    ) -> Dict[str, Any]:
        """
        Upload text content to RAG store

        Content will be saved as a temporary markdown file; the temporary file is deleted after upload.

        Args:
            agent_id: Agent ID, used to locate the store
            user_id: User ID, used to locate the store
            content: Text content to upload
            wait_seconds: Time to wait for indexing completion (seconds), default 5

        Returns:
            Dict[str, Any]: Upload result
            - success: bool, whether successful
            - store_name: str, store name (on success)
            - temp_filename: str, generated temporary filename
            - message: str, result message
            - error: str, error message (on failure)

        Example:
            result = GeminiRAGTrigger.upload_text(
                agent_id="agent_123",
                user_id="user_456",
                content="# Meeting Notes\\n\\nImportant decisions..."
            )
        """
        try:
            # Validate content is not empty
            if not content or not content.strip():
                return {
                    "success": False,
                    "error": "Content cannot be empty"
                }

            logger.info(f"[GeminiRAGTrigger] Uploading text content ({len(content)} characters)")
            result = GeminiRAGModule.upload_text_to_store(
                agent_id=agent_id,
                content=content,
                wait_seconds=wait_seconds
            )
            logger.info("[GeminiRAGTrigger] Text upload successful")
            return result

        except Exception as e:
            logger.exception(f"[GeminiRAGTrigger] Text upload failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    @staticmethod
    def upload_files_batch(
        agent_id: str,
        user_id: str,
        file_paths: List[str],
        wait_seconds: int = 5
    ) -> Dict[str, Any]:
        """
        Batch upload multiple files to RAG store

        Uploads all files in the list sequentially and aggregates results.

        Args:
            agent_id: Agent ID, used to locate the store
            user_id: User ID, used to locate the store
            file_paths: List of file paths to upload
            wait_seconds: Time to wait for indexing per file (seconds)

        Returns:
            Dict[str, Any]: Batch upload result
            - success: bool, whether all succeeded
            - total: int, total file count
            - successful: int, number of successful uploads
            - failed: int, number of failed uploads
            - results: List[Dict], individual result for each file

        Example:
            result = GeminiRAGTrigger.upload_files_batch(
                agent_id="agent_123",
                user_id="user_456",
                file_paths=["/data/doc1.pdf", "/data/doc2.txt"]
            )
            print(f"Successful: {result['successful']}/{result['total']}")
        """
        results = []
        successful = 0
        failed = 0

        for file_path in file_paths:
            result = GeminiRAGTrigger.upload_file(
                agent_id=agent_id,
                user_id=user_id,
                file_path=file_path,
                wait_seconds=wait_seconds
            )
            results.append({
                "file_path": file_path,
                **result
            })
            if result.get("success"):
                successful += 1
            else:
                failed += 1

        return {
            "success": failed == 0,
            "total": len(file_paths),
            "successful": successful,
            "failed": failed,
            "results": results
        }

    # =========================================================================
    # Query Methods
    # =========================================================================

    @staticmethod
    def query(
        agent_id: str,
        user_id: str,
        query: str,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Query documents in RAG store

        Uses natural language for semantic search, returns the most relevant document chunks.

        Args:
            agent_id: Agent ID, used to locate the store
            user_id: User ID, used to locate the store
            query: Natural language query
            top_k: Maximum number of results to return, default 5

        Returns:
            List[Dict[str, Any]]: List of retrieved text chunks, each element contains:
            - text: str, retrieved text content
            - title: str, source document title

        Example:
            chunks = GeminiRAGTrigger.query(
                agent_id="agent_123",
                user_id="user_456",
                query="What are the main findings?"
            )
            for chunk in chunks:
                print(f"Source: {chunk['title']}")
                print(f"Content: {chunk['text']}")
        """
        try:
            logger.debug(f"[GeminiRAGTrigger] Querying: {query[:50]}...")
            chunks = GeminiRAGModule.query_store(
                agent_id=agent_id,
                query=query,
                top_k=top_k
            )
            logger.debug(f"[GeminiRAGTrigger] Found {len(chunks)} results")
            return chunks

        except Exception as e:
            logger.exception(f"[GeminiRAGTrigger] Query failed: {e}")
            return []

    # =========================================================================
    # Store Management Methods
    # =========================================================================

    @staticmethod
    def get_store_name(agent_id: str, user_id: str) -> Optional[str]:
        """
        Get the store name for a given agent-user pair

        Args:
            agent_id: Agent ID
            user_id: User ID

        Returns:
            Optional[str]: Store name, or None if it doesn't exist
        """
        display_name = GeminiRAGModule._get_display_name(agent_id)
        store_map = GeminiRAGModule._load_store_map()
        return store_map.get(display_name)

    @staticmethod
    def ensure_store_exists(agent_id: str, user_id: str) -> str:
        """
        Ensure the store for a given agent-user pair exists; create if it doesn't

        Args:
            agent_id: Agent ID
            user_id: User ID

        Returns:
            str: Store name
        """
        store = GeminiRAGModule._get_or_create_store(agent_id)
        return store.name

    @staticmethod
    def list_stores() -> Dict[str, str]:
        """
        List all stores in the mapping file

        Returns:
            Dict[str, str]: display_name -> store_name mapping dictionary
        """
        return GeminiRAGModule._load_store_map()

    @staticmethod
    def get_display_name(agent_id: str, user_id: str) -> str:
        """
        Get the display_name for a given agent-user pair

        Args:
            agent_id: Agent ID
            user_id: User ID

        Returns:
            str: Formatted display_name, format: agent_{agent_id}_user_{user_id}
        """
        return GeminiRAGModule._get_display_name(agent_id)


# =============================================================================
# CLI Entry Point (for testing)
# =============================================================================

def main():
    """
    Command-line entry point for testing GeminiRAGTrigger functionality.

    Supported operations:
    - --upload: Upload a file
    - --text: Upload text content
    - --query: Query documents
    - --list-stores: List all stores
    """
    import argparse
    import xyz_agent_context.settings  # noqa: F401 - Ensure .env is loaded

    parser = argparse.ArgumentParser(
        description="GeminiRAGTrigger - Document Upload Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage examples:
  # Upload a file
  uv run python -m xyz_agent_context.module.gemini_rag_module.gemini_rag_trigger \\
      --agent-id agent_test --user-id user_test --upload /path/to/file.pdf

  # Upload text content
  uv run python -m xyz_agent_context.module.gemini_rag_module.gemini_rag_trigger \\
      --agent-id agent_test --user-id user_test --text "Important content..."

  # Query documents
  uv run python -m xyz_agent_context.module.gemini_rag_module.gemini_rag_trigger \\
      --agent-id agent_test --user-id user_test --query "What is the topic?"

  # List all stores
  uv run python -m xyz_agent_context.module.gemini_rag_module.gemini_rag_trigger --list-stores
"""
    )

    parser.add_argument("--agent-id", type=str, help="Agent ID")
    parser.add_argument("--user-id", type=str, help="User ID")
    parser.add_argument("--upload", type=str, help="File path to upload")
    parser.add_argument("--text", type=str, help="Text content to upload")
    parser.add_argument("--query", type=str, help="Query content")
    parser.add_argument("--top-k", type=int, default=5, help="Number of query results to return")
    parser.add_argument("--list-stores", action="store_true", help="List all stores")

    args = parser.parse_args()

    # List all stores
    if args.list_stores:
        stores = GeminiRAGTrigger.list_stores()
        logger.info("\nAll Stores:")
        logger.info("-" * 60)
        if stores:
            for display_name, store_name in stores.items():
                logger.info(f"  {display_name}: {store_name}")
        else:
            logger.info("  No stores found")
        logger.info("")
        return

    # Check required parameters
    if not args.agent_id or not args.user_id:
        logger.error("Upload/query operations require --agent-id and --user-id parameters")
        return

    # Upload file
    if args.upload:
        logger.info(f"\nUploading file: {args.upload}")
        result = GeminiRAGTrigger.upload_file(
            agent_id=args.agent_id,
            user_id=args.user_id,
            file_path=args.upload
        )
        logger.info(f"Result: {result}")

    # Upload text
    elif args.text:
        logger.info(f"\nUploading text ({len(args.text)} characters)")
        result = GeminiRAGTrigger.upload_text(
            agent_id=args.agent_id,
            user_id=args.user_id,
            content=args.text
        )
        logger.info(f"Result: {result}")

    # Query documents
    elif args.query:
        logger.info(f"\nQuerying: {args.query}")
        chunks = GeminiRAGTrigger.query(
            agent_id=args.agent_id,
            user_id=args.user_id,
            query=args.query,
            top_k=args.top_k
        )
        logger.info(f"\nFound {len(chunks)} results:")
        logger.info("-" * 60)
        for i, chunk in enumerate(chunks, 1):
            logger.info(f"\n[Chunk {i}] Source: {chunk.get('title', 'Unknown')}")
            logger.info(f"Content: {chunk.get('text', '')[:500]}...")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
