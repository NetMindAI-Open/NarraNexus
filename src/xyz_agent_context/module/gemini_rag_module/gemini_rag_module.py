"""
Gemini RAG Module - Document Retrieval Module based on Gemini File Search API

@file_name: gemini_rag_module.py
@author: NetMind.AI
@date: 2025-12-02
@description: Implements RAG (Retrieval Augmented Generation) capability using Google Gemini File Search API

=============================================================================
Module Overview
=============================================================================

GeminiRAGModule enables Agent to store and retrieve documents using Google Gemini File Search API.
Each Agent has a dedicated file search storage space (store), shared among all users.

Core capabilities:
1. **Instructions** - Guides Agent on when/how to use RAG for information retrieval
2. **Tools (MCP)** - Provides three MCP tools:
    - rag_query: Query documents in the store
    - rag_upload_file: Upload files to the store
    - rag_upload_text: Upload text content as documents

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │                     GeminiRAGModule                          │
    ├─────────────────────────────────────────────────────────────┤
    │  MCP Tools:                                                  │
    │    rag_query, rag_upload_file, rag_upload_text               │
    ├─────────────────────────────────────────────────────────────┤
    │  Store Management:                                           │
    │    - Each agent_id maps to an independent store (shared)     │
    │    - Mapping saved in ~/.nexusagent/data/                     │
    └─────────────────────────────────────────────────────────────┘
                                │
                                ▼
    ┌─────────────────────────────────────────────────────────────┐
    │                  Gemini File Search API                      │
    │              Google Cloud Document Search Service             │
    └─────────────────────────────────────────────────────────────┘

Store naming convention:
- display_name format: agent_{agent_id}
- Example: agent_ecb12faf

Dependencies:
- google-genai: Google Generative AI SDK
- Environment variable: GOOGLE_API_KEY
"""

import os
import json
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import uuid4

from loguru import logger

# Module base class
from xyz_agent_context.module import XYZBaseModule
from xyz_agent_context.agent_framework.gemini_api_sdk import GeminiAPISDK

# Schema definitions
from xyz_agent_context.schema import (
    KeywordsUpdateRequest,
    ModuleConfig,
    MCPServerConfig,
    ContextData,
)

# Utilities
from xyz_agent_context.utils import DatabaseClient

# Repository
from xyz_agent_context.repository import RAGStoreRepository

# Utils (docling disabled, no longer need convert_document_to_markdown)
# from xyz_agent_context.module.gemini_rag_module.rag_file_service import convert_document_to_markdown


class GeminiRAGModule(XYZBaseModule):
    """
    Gemini RAG Module - RAG implementation based on Gemini File Search API

    This module provides document storage and semantic retrieval capabilities for Agent:

    1. **Instructions** - Tells Agent when to use RAG for information retrieval
       - When user asks about previously uploaded documents
       - When specific information needs to be found from stored files
       - When user mentions "my documents", "uploaded files", etc.

    2. **Tools (MCP)** - Provides three core tools
       - rag_query: Query documents using natural language, returns relevant text chunks
       - rag_upload_file: Upload files to knowledge base
       - rag_upload_text: Upload text content directly to knowledge base

    3. **Store Management** - Manages Agent-dedicated file search storage space
       - Each agent_id maps to an independent store (shared among all users)
       - Mapping persisted to local JSON file

    Store naming convention:
    - display_name: agent_{agent_id}
    - Mapping file path: ~/.nexusagent/data/gemini_file_search_map.json

    Usage examples:
        # Via MCP tools (Agent auto-invokes)
        rag_query(agent_id="agent_xxx", query="What is the document about?")

        # Via static methods (direct code call)
        chunks = GeminiRAGModule.query_store(agent_id, query)
    """

    # =========================================================================
    # Class Constants
    # =========================================================================

    # Store mapping file path - saves display_name -> store_name mapping
    STORE_MAP_FILE = Path.home() / ".nexusagent" / "data" / "gemini_file_search_map.json"

    # Temporary file directory - used for creating temp .md files during upload_text
    TEMP_DIR = Path.home() / ".nexusagent" / "data" / "gemini_rag_temp"

    # =========================================================================
    # Initialization
    # =========================================================================

    def __init__(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        database_client: Optional[DatabaseClient] = None,
        instance_id: Optional[str] = None,
        instance_ids: Optional[List[str]] = None
    ):
        """
        Initialize GeminiRAGModule

        RAG is an Agent-level module, all users share the same knowledge base.

        Args:
            agent_id: Agent ID, used for data isolation and store naming
            user_id: User ID (Agent-level module, usually None)
            database_client: Database client (optional, this module mainly uses file storage)
            instance_id: Instance ID (if provided, indicates this is a specific instance operation)
            instance_ids: All instance IDs associated with Narrative

        Initialization flow:
        1. Call parent initialization
        2. Set MCP Server port
        3. Ensure necessary directories exist
        4. Set Agent instructions
        """
        super().__init__(agent_id, user_id, database_client, instance_id, instance_ids)

        # MCP Server port (avoid conflicts with other modules)
        self.port = 7805

        # Ensure necessary directories exist
        self.STORE_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.TEMP_DIR.mkdir(parents=True, exist_ok=True)

        # Agent instruction template - contains keyword placeholder {rag_keywords}
        # Note: English is used here because it's for the LLM
        # {rag_keywords} will be replaced with actual keywords during hook_data_gathering
        self.instructions_template = """
## RAG Module - Document Search Capability

You have access to a personal knowledge base (RAG system) for storing and retrieving documents.

### Knowledge Base Content Summary (Keywords)

{rag_keywords}

### When to Use RAG

- When the user's question is related to the knowledge base keywords.
- When the user explicitly asks to search documents or use RAG to answer the question.

If any of the above conditions are met, you must call the `rag_query` tool to perform the search，otherwise, you can't call the `rag_query` tool.


note: Your query should combine the user's question with the existing keywords and any keywords likely to appear in the answer. Optimize and expand the query to improve its comprehensiveness, accuracy, and relevance.

### Available Tools

1. **rag_query** - Search documents with natural language
    - Returns relevant text chunks with source info
    - Use specific queries for better results

2. **rag_upload_file** - Upload a file to the knowledge base
    - Supports PDF, TXT, MD, and other text formats
    - File will be indexed for future searches

3. **rag_upload_text** - Store text content directly
    - Good for saving important information
    - Content will be indexed for future searches
"""
        # Default instructions (when no keywords exist)
        self.instructions = self.instructions_template.replace(
            "{rag_keywords}",
            "*(Knowledge base is empty. No documents uploaded yet.)*"
        )

    # =========================================================================
    # Module Configuration
    # =========================================================================

    def get_config(self) -> ModuleConfig:
        """
        Return GeminiRAGModule configuration

        Returns:
            ModuleConfig: Configuration object containing module name, priority, enabled status, and description
        """
        return ModuleConfig(
            name="GeminiRAGModule",
            priority=5,  # Medium priority
            enabled=True,
            description="Provides document storage and semantic retrieval capability based on Gemini File Search"
        )

    # =========================================================================
    # Hooks
    # =========================================================================

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        """
        Collect RAG-related data and populate ContextData

        Called before each Agent execution, retrieves Agent's knowledge base keywords from database
        and replaces the placeholder in the instructions template.

        RAG is an Agent-level module, all users share the same knowledge base.

        Flow:
        1. Get RAG Store record from database
        2. Get keywords list
        3. Format keywords and replace {rag_keywords} in instructions template
        4. Update self.instructions

        Args:
            ctx_data: Context data object

        Returns:
            Updated ContextData
        """
        logger.debug("          → GeminiRAGModule.hook_data_gathering() started")

        try:
            repo = RAGStoreRepository(self.db)
            keywords = []

            # Prefer using instance_id to get keywords
            if self.instance_id:
                keywords = await repo.get_keywords_by_instance(
                    instance_id=self.instance_id
                )
                logger.debug(f"          Using instance_id={self.instance_id} for RAG keywords")
            else:
                # Fall back to agent_id (Agent level, no user_id)
                keywords = await repo.get_keywords(
                    agent_id=self.agent_id,
                    user_id=None
                )
                logger.debug("          Using agent_id for RAG keywords")

            if not keywords:
                logger.debug("          No RAG keywords found, knowledge base may be empty")
                ctx_data.rag_keywords = []

            # Format keywords
            if keywords:
                keywords_display = f"**Keywords in your knowledge base:** {GeminiRAGModule.format_keywords_for_display(keywords)}"
                logger.debug(f"          RAG keywords: {keywords}")
            else:
                keywords_display = "*(Knowledge base is empty. No documents uploaded yet.)*"
                logger.debug("          RAG knowledge base is empty")

            # Update instructions
            self.instructions = self.instructions_template.replace(
                "{rag_keywords}",
                keywords_display
            )

            # Also save keywords to ctx_data (for other modules to use)
            ctx_data.rag_keywords = keywords

            logger.debug("          ← GeminiRAGModule.hook_data_gathering() completed")

        except Exception as e:
            logger.error(f"Error in GeminiRAGModule.hook_data_gathering: {e}")
            # Use default instructions on error
            self.instructions = self.instructions_template.replace(
                "{rag_keywords}",
                "*(Error loading knowledge base. Please try again later.)*"
            )
            ctx_data.rag_keywords = []

        return ctx_data

    # =========================================================================
    # Store Management (static methods, for cross-module calls)
    # =========================================================================

    @staticmethod
    def _load_store_map() -> Dict[str, str]:
        """
        Load store mapping from local JSON file

        Mapping structure:
        {
            "agent_xxx_user_yyy": "fileSearchStores/abc123...",
            ...
        }

        Returns:
            Dict[str, str]: display_name -> store_name mapping dictionary
                           Returns empty dict if file doesn't exist or read fails
        """
        if GeminiRAGModule.STORE_MAP_FILE.exists():
            try:
                with open(GeminiRAGModule.STORE_MAP_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load store mapping: {e}")
        return {}

    @staticmethod
    def _save_store_map(store_map: Dict[str, str]) -> None:
        """
        Save store mapping to local JSON file

        Args:
            store_map: display_name -> store_name mapping dictionary
        """
        try:
            # Ensure parent directory exists
            GeminiRAGModule.STORE_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(GeminiRAGModule.STORE_MAP_FILE, "w", encoding="utf-8") as f:
                json.dump(store_map, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save store mapping: {e}")

    @staticmethod
    def _get_display_name(agent_id: str) -> str:
        """
        Generate store display_name from agent_id

        Naming convention: agent_{agent_id}
        Example: agent_ecb12faf

        Args:
            agent_id: Agent ID

        Returns:
            str: Formatted display_name
        """
        return f"agent_{agent_id}"

    @staticmethod
    def _get_gemini_client():
        """
        Get Gemini API client

        Reads API key from GOOGLE_API_KEY environment variable and creates client.

        Returns:
            genai.Client: Gemini API client instance

        Raises:
            ValueError: If GOOGLE_API_KEY environment variable is not set
        """
        from google import genai
        from xyz_agent_context.agent_framework.api_config import gemini_config

        if not gemini_config.api_key:
            raise ValueError("Environment variable GOOGLE_API_KEY is not set")
        return genai.Client(api_key=gemini_config.api_key)

    @staticmethod
    def _get_or_create_store(agent_id: str):
        """
        Get or create a file search store

        Processing flow:
        1. Generate display_name from agent_id
        2. Check if local mapping already has this store
        3. If yes, try to get the store from Gemini API
        4. If retrieval fails (store may have been deleted), remove invalid mapping and create new store
        5. If no, create new store and save mapping

        Args:
            agent_id: Agent ID

        Returns:
            store: Gemini FileSearchStore object
        """
        display_name = GeminiRAGModule._get_display_name(agent_id)
        store_map = GeminiRAGModule._load_store_map()
        client = GeminiRAGModule._get_gemini_client()

        # Check if store already exists in mapping
        if display_name in store_map:
            store_name = store_map[display_name]
            try:
                # Try to get existing store from API
                store = client.file_search_stores.get(name=store_name)
                logger.debug(f"Found existing store: {store_name}")
                return store
            except Exception as e:
                # Store may have been deleted, clean up invalid mapping
                logger.warning(f"Failed to get store {store_name}, will create new one: {e}")
                del store_map[display_name]
                GeminiRAGModule._save_store_map(store_map)

        # Create new store
        store = client.file_search_stores.create(
            config={"display_name": display_name}
        )
        logger.info(f"Created new FileSearchStore: {store.name} (display_name: {display_name})")

        # Save mapping
        store_map[display_name] = store.name
        GeminiRAGModule._save_store_map(store_map)

        return store

    # =========================================================================
    # Core Operations (static methods, for MCP tools and external calls)
    # =========================================================================

    @staticmethod
    def query_store(agent_id: str, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Query documents in the store

        Uses Gemini File Search for semantic retrieval, returns document chunks most relevant to the query.

        Implementation:
        1. Get or create the store corresponding to the agent
        2. Call Gemini API with file_search as a tool
        3. API automatically retrieves relevant content from the store
        4. Extract retrieved text chunks from the response's grounding_metadata

        Args:
            agent_id: Agent ID, used to locate the store
            query: Natural language query content
            top_k: Maximum number of results to return, default 5

        Returns:
            List[Dict[str, Any]]: List of retrieved text chunks, each element contains:
                - text: Text content
                - title: Source document title
        """
        from google.genai import types

        # Get or create store
        store = GeminiRAGModule._get_or_create_store(agent_id)
        client = GeminiRAGModule._get_gemini_client()

        # Call Gemini API to perform retrieval
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=query,
            config=types.GenerateContentConfig(
                tools=[
                    types.Tool(
                        file_search=types.FileSearch(
                            file_search_store_names=[store.name],
                            top_k=top_k
                        )
                    )
                ],
            )
        )

        # Log token usage (for cost monitoring)
        if response.usage_metadata:
            usage = response.usage_metadata
            logger.debug(
                f"RAG query - input: {usage.prompt_token_count}, "
                f"output: {usage.candidates_token_count}, "
                f"total: {usage.total_token_count}"
            )

        # Extract retrieved text chunks from response
        chunks = []
        for candidate in response.candidates:
            if candidate.grounding_metadata and candidate.grounding_metadata.grounding_chunks:
                for chunk in candidate.grounding_metadata.grounding_chunks:
                    chunks.append({
                        "text": chunk.retrieved_context.text,
                        "title": chunk.retrieved_context.title,
                    })

        return chunks

    @staticmethod
    def upload_file_to_store(
        agent_id: str,
        file_path: str,
        wait_seconds: int = 5
    ) -> Dict[str, Any]:
        """
        Upload file to store

        Uploads the file at the specified path to Gemini File Search store, file will be automatically indexed.

        Note: If the file path contains non-ASCII characters (e.g., Chinese), a temporary file
        will be automatically created to avoid encoding errors.

        Args:
            agent_id: Agent ID, used to locate the store
            file_path: Absolute path of the file to upload
            wait_seconds: Time to wait for indexing completion (seconds), default 5 seconds

        Returns:
            Dict[str, Any]: Upload result containing:
                - success: Whether successful
                - store_name: Store name
                - file_path: Uploaded file path
                - message: Result message

        Raises:
            FileNotFoundError: If the file does not exist
        """
        import shutil

        # Get or create store
        store = GeminiRAGModule._get_or_create_store(agent_id)
        client = GeminiRAGModule._get_gemini_client()

        # Check if file exists
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        logger.info(f"File exists: {file_path}")
        # Check if file path contains non-ASCII characters
        # If so, create temporary file to avoid encoding errors
        temp_file_path = None
        upload_path = file_path
        
        try:
            # Check if filename contains non-ASCII characters
            file_path_str = str(file_path)
            try:
                file_path_str.encode('ascii')
                # Filename only contains ASCII characters, can be used directly
            except UnicodeEncodeError:
                import hashlib
                # Filename contains non-ASCII characters, need to create temporary file
                logger.debug(f"Filename contains non-ASCII characters, creating temp file: {file_path}")

                # Get original file extension
                original_filename = Path(file_path).name
                original_ext = Path(file_path).suffix  # Get original file extension
                hash_obj = hashlib.sha256(original_filename.encode('utf-8'))
                hash_hex = hash_obj.hexdigest()[:16]  # Take first 16 characters
                # Generate temp filename (ASCII characters only)
                temp_filename = f"{hash_hex}{original_ext}"
                temp_file_path = GeminiRAGModule.TEMP_DIR / temp_filename
                
                # Ensure temp directory exists
                GeminiRAGModule.TEMP_DIR.mkdir(parents=True, exist_ok=True)
                
                # Copy file to temp location
                shutil.copy2(file_path, temp_file_path)
                upload_path = str(temp_file_path)
                logger.debug(f"Created temp file: {upload_path}")

            # Upload file to store
            operation = client.file_search_stores.upload_to_file_search_store(
                file_search_store_name=store.name,
                file=upload_path
            )
            logger.info(f"Upload operation started: {operation}")

            # Wait for indexing to complete (Gemini indexing is asynchronous)
            time.sleep(wait_seconds)

            return {
                "success": True,
                "store_name": store.name,
                "file_path": file_path,
                "message": "File uploaded and indexed successfully"
            }

        finally:
            # Clean up temporary file
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                    logger.debug(f"Deleted temporary file: {temp_file_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete temporary file: {temp_file_path}, error: {e}")

    @staticmethod
    def upload_text_to_store(
        agent_id: str,
        content: str,
        wait_seconds: int = 5
    ) -> Dict[str, Any]:
        """
        Upload text content to store

        Saves text content as a temporary .md file, uploads it, then deletes the temporary file.

        Processing flow:
        1. Generate temporary filename (date_time_randomcode.md)
        2. Write content to temporary file
        3. Call upload_file_to_store to upload
        4. Delete temporary file (ensured in finally block)

        Args:
            agent_id: Agent ID, used to locate the store
            content: Text content to upload
            wait_seconds: Time to wait for indexing completion (seconds), default 5

        Returns:
            Dict[str, Any]: Upload result containing:
                - success: Whether successful
                - store_name: Store name
                - temp_filename: Generated temporary filename
                - message: Result message
        """
        # Generate temporary filename: date_time_randomcode.md
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = uuid4().hex[:8]
        temp_filename = f"{timestamp}_{random_suffix}.md"
        temp_path = GeminiRAGModule.TEMP_DIR / temp_filename

        # Ensure temporary directory exists
        GeminiRAGModule.TEMP_DIR.mkdir(parents=True, exist_ok=True)

        try:
            # Write to temporary file
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.debug(f"Created temporary file: {temp_path}")

            # Upload file
            result = GeminiRAGModule.upload_file_to_store(
                agent_id=agent_id,
                file_path=str(temp_path),
                wait_seconds=wait_seconds
            )

            # Add temporary filename to return result
            result["temp_filename"] = temp_filename
            return result

        finally:
            # Ensure temporary file is deleted (regardless of upload success)
            if temp_path.exists():
                temp_path.unlink()
                logger.debug(f"Deleted temporary file: {temp_path}")

    # =========================================================================
    # Keyword Management (static methods)
    # =========================================================================

    @staticmethod
    def generate_keywords_score(n: int, c: float = 0.05,s:float=100):
        """
        n: Number of keywords
        c: Controls concentration degree; larger values concentrate weight toward the front. Recommended range ~0.01..0.5.
        Returns a normalized weight list of length n (sum equals s).
        """
        assert n >= 1
        assert c >= 0
        a = 1.0 / (1.0 + c * (n - 1))
        raw = [a ** i for i in range(n)]
        sum_raw = sum(raw)
        ret_list=[x*s/ sum_raw for x in raw]
        ret_list[0]=s  # Ensure the first item has the maximum value
        return ret_list


    @staticmethod
    async def update_keywords_with_llm(
        agent_id: str,
        new_file_content: str = None,
        new_filename: str = None,
        db_client: Optional[Any] = None
    ) -> List[str]:
        """
        Intelligently update knowledge base keywords using LLM

        Called when a new file is uploaded:
        1. Get existing keywords
        2. Call LLM to analyze new file content
        3. Generate updated keyword list
        4. Update database

        Args:
            agent_id: Agent ID
            new_file_content: Content of the newly uploaded file (recommended first 3000 characters)
            new_filename: New filename
            db_client: Database client

        Returns:
            Updated keyword list
        """

        # Use repository
        repo = RAGStoreRepository(db_client)

        # Get existing keywords (Agent level, no user_id)
        current_keywords = await repo.get_keywords(agent_id, None, score=True)
        file_count = await repo.get_file_count(agent_id, None)
        
        # Build LLM prompt - optimized version
        instructions = """You are a document keyword extraction expert.

Your task is to analyze document content and extract precise, representative keywords that capture the document's core topics for fast topical indexing for search/discovery..

## Requirements

1. **Precision over quantity**: Extract only keywords that accurately represent the document's main topics
2. **Specificity**: Use specific terms rather than generic ones
    - Good: "Transformer architecture", "attention mechanism", "BERT model"
    - Bad: "machine learning", "technology", "method"
3. **Domain terminology**: Preserve professional/technical terms as-is
4. **Named entities**: Include important names, products, frameworks, or concepts
5. **Bilingual support**: Keep keywords in the document's original language
6. **keywords or phrases**: short phrases only (no sentences, no explanations). 1–4 words per phrase; hyphens allowed; no punctuation otherwise.
7. **keywords count**: Extract 5-20 keywords or phrases and keywords should be ordered by importance
8. **merge**: deduplicate and collapse near-synonyms.
"""

        # Extract keywords from the provided file content
        user_input = f"""
## New Document Content
Filename: {new_filename}

{new_file_content}

Please analyze and generate an updated keywords list and reasoning from the new document content.
"""

        sdk = GeminiAPISDK()
        response = await sdk.llm_function(
            instructions=instructions,
            user_input=user_input,
            output_type=KeywordsUpdateRequest,
        )
        text = response.candidates[0].content.parts[0].text
        data = json.loads(text)
        new_keywords = data['keywords']

        new_keywords_score = GeminiRAGModule.generate_keywords_score(len(new_keywords))
        new_keywords = [{"keyword": keyword, "score": score} for keyword, score in zip(new_keywords, new_keywords_score)]
        m, n = len(current_keywords), len(new_keywords)
        merged_keywords = []
        i, j = 0, 0
        while i < m and j < n:
            if current_keywords[i]["score"] > new_keywords[j]["score"]:
                merged_keywords.append(current_keywords[i])
                i += 1
            else:
                merged_keywords.append(new_keywords[j])
                j += 1
        while i < m:
            merged_keywords.append(current_keywords[i])
            i += 1
        while j < n:
            merged_keywords.append(new_keywords[j])
            j += 1

        # Update database (Agent level, no user_id)
        merged_keywords = merged_keywords[:min(max(100, file_count * 2), len(merged_keywords))]
        await repo.update_keywords(agent_id, None, merged_keywords)
        merged_keywords = merged_keywords[:min((len(merged_keywords), file_count * 10))]
        logger.info(f"Keywords updated for agent {agent_id}: {new_keywords}")
        return [item["keyword"] for item in current_keywords], [item["keyword"] for item in new_keywords]


    @staticmethod
    def format_keywords_for_display(keywords: List[str]) -> str:
        """
        Format keywords for display in Agent instructions

        Args:
            keywords: List of keywords

        Returns:
            Formatted string
        """
        if not keywords:
            return "(Knowledge base is empty, no documents uploaded yet)"

        return ", ".join(keywords)

    # =========================================================================
    # MCP Server Configuration and Creation
    # =========================================================================

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        """
        Return MCP Server configuration

        Returns:
            MCPServerConfig: MCP server configuration containing server name, URL, and type
        """
        return MCPServerConfig(
            server_name="gemini_rag_module",
            server_url=f"http://127.0.0.1:{self.port}/sse",
            type="sse"
        )

    def create_mcp_server(self) -> Optional[Any]:
        """
        Create MCP Server instance

        Delegates tool registration to _rag_mcp_tools module.

        Returns:
            FastMCP: Configured MCP server instance
        """
        from xyz_agent_context.module.gemini_rag_module._rag_mcp_tools import create_rag_mcp_server
        return create_rag_mcp_server(self.port, GeminiRAGModule)
