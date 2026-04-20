"""
@file_name: context_schema.py
@author: NetMind.AI
@date: 2025-11-15
@description: Context related data models

Includes:
- ContextData - Data collected during Context construction
- ContextRuntimeOutput - Output of ContextRuntime
"""

from typing import List, Dict, Any, Optional, Union
from pydantic import BaseModel, ConfigDict

from xyz_agent_context.schema.hook_schema import WorkingSource


class ContextData(BaseModel):
    model_config = ConfigDict(extra='allow')
    """
    Data collected during Context construction

    This class is continuously expanded during the Context construction process:
    - Extracts data from Narratives
    - Obtains data from Module's data_gathering
    - Adds user input, time, and other basic information

    Following the example in the design document:
    ContextData:
        - User input: Hello
        - Chat module
        - Basic info of User in Social-network
        - Current time information
        - Basic info
        - ...
    """
    agent_id: str
    user_id: Optional[str] = None
    input_content: str  # Current user input
    narrative_id: Optional[str] = None  # Current Narrative ID (for Memory isolation)

    # The following fields are populated during data_gathering
    chat_history: Optional[List[Dict[str, Any]]] = None
    user_profile: Optional[Dict[str, Any]] = None
    current_time: Optional[str] = None
    working_source: Optional[Union[WorkingSource, str]] = None  # Supports WorkingSource enum or string

    # Agent basic info (populated by BasicInfoModule)
    agent_name: Optional[str] = None  # Agent name
    agent_description: Optional[str] = None  # Agent description
    creator_id: Optional[str] = None  # Creator ID (boss)
    is_creator: Optional[bool] = None  # Whether the current conversation user is the Creator
    bootstrap_active: bool = False  # Whether bootstrap mode is active for this context
    user_role: Optional[str] = None  # Current user role description ("Creator (Boss)" or "User/Customer")

    # Deployment environment (populated by BasicInfoModule.hook_data_gathering).
    # Short tag + verbose description block — both rendered into the system
    # prompt so the agent can reason about cloud vs local constraints.
    deployment_mode: Optional[str] = None  # "cloud" | "local"
    deployment_context: Optional[str] = None  # Full prose block for the prompt

    # RAG Module data (populated by GeminiRAGModule)
    rag_keywords: Optional[List[str]] = None  # Knowledge base keyword list

    # For storing arbitrary extra data (Modules can add custom fields)
    extra_data: Dict[str, Any] = {}


class ContextRuntimeOutput(BaseModel):
    """
    Output of ContextRuntime

    Contains the constructed messages and mcp_urls, ready to be passed to the Agent Framework
    """
    messages: List[Dict[str, Any]]  # messages list (includes system prompt and history messages)
    mcp_urls: Dict[str, str]  # MCP server URLs (module_name -> url)
    ctx_data: ContextData  # ContextData (contains all collected data)
