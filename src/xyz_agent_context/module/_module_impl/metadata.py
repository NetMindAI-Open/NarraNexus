"""
@file_name: metadata.py
@author: NetMind.AI
@date: 2025-12-22
@description: Module metadata management

Provides metadata information for all available Modules, used for:
1. Understanding each Module's capabilities during LLM decision-making
2. Reference information during Instance management
3. Documentation generation
"""

from typing import Dict, List, Any


# ===== Module Metadata Definition =====
# Detailed description of each Module, including capabilities, use cases, Instance management, etc.

MODULE_METADATA: Dict[str, Dict[str, Any]] = {
    "ChatModule": {
        "name": "ChatModule",
        "description": "Provides messaging capabilities (instant chat + Inbox notifications)",
        "capabilities": [
            "Receive and process user messages",
            "Maintain conversation history and context",
            "Send async notifications to users via Inbox",
        ],
        "instance_type": "persistent",  # Persistent: remains once added
        "typical_instance_id": "chat_{uuid8}",
        "use_cases": [
            "Daily conversations and Q&A",
            "Main entry point for user interaction",
            "Message notifications and reminders",
        ],
        "priority": 1,
    },

    "JobModule": {
        "name": "JobModule",
        "description": "Provides background task creation and management capabilities",
        "capabilities": [
            "Create and manage background tasks",
            "Task progress tracking",
            "Callback notifications on task completion",
            "Support for task dependencies",
        ],
        "instance_type": "task",  # Task-type: deleted after completion
        "typical_instance_id": "job_{uuid8}",
        "use_cases": [
            "Long-running background tasks",
            "Multi-step complex workflows",
            "Tasks requiring progress tracking",
        ],
        "priority": 4,
    },

    "SocialNetworkModule": {
        "name": "SocialNetworkModule",
        "description": "Provides social network related capabilities",
        "capabilities": [
            "Manage user social relationships",
            "Handle social interactions (follow, friends, etc.)",
            "Social network data analysis",
        ],
        "instance_type": "persistent",
        "typical_instance_id": "social_{uuid8}",
        "use_cases": [
            "Social feature related conversations",
            "User relationship management",
            "Social data queries",
        ],
        "priority": 3,
    },

    "AwarenessModule": {
        "name": "AwarenessModule",
        "description": "Provides environmental awareness and context understanding capabilities",
        "capabilities": [
            "Perceive current time, date and other environmental info",
            "Understand user's situation and intent",
            "Provide context-relevant suggestions",
        ],
        "instance_type": "persistent",
        "typical_instance_id": "aware_{uuid8}",
        "use_cases": [
            "Time-aware conversations",
            "Context-relevant recommendations",
            "Environment-related queries",
        ],
        "priority": 2,
    },

    "BasicInfoModule": {
        "name": "BasicInfoModule",
        "description": "Provides basic information management capabilities",
        "capabilities": [
            "Manage user basic information",
            "Store and retrieve preference settings",
            "Maintain user profiles",
        ],
        "instance_type": "persistent",
        "typical_instance_id": "info_{uuid8}",
        "use_cases": [
            "User information queries",
            "Preference settings management",
            "Personalized services",
        ],
        "priority": 2,
    },

}


def get_module_metadata(module_name: str) -> Dict[str, Any]:
    """
    Get metadata for the specified Module

    Args:
        module_name: Module name

    Returns:
        Module metadata dictionary, returns empty dict if not found
    """
    return MODULE_METADATA.get(module_name, {})


def get_all_modules_metadata() -> str:
    """
    Get metadata for all Modules, formatted as LLM-readable text

    Returns:
        Formatted Modules metadata text
    """
    lines = []

    for module_name, metadata in MODULE_METADATA.items():
        lines.append(f"## {module_name}")
        lines.append(f"- **Description**: {metadata['description']}")
        lines.append(f"- **Type**: {metadata['instance_type']} ({'persistent, retained once added' if metadata['instance_type'] == 'persistent' else 'task-type, deleted after completion'})")
        lines.append(f"- **Typical Instance ID**: `{metadata['typical_instance_id']}`")
        lines.append(f"- **Priority**: {metadata['priority']}")

        lines.append("- **Capabilities**:")
        for cap in metadata['capabilities']:
            lines.append(f"  - {cap}")

        lines.append("- **Use Cases**:")
        for use_case in metadata['use_cases']:
            lines.append(f"  - {use_case}")

        lines.append("")  # Empty line separator

    return "\n".join(lines)


def get_available_module_names() -> List[str]:
    """
    Get list of all available Module names

    Returns:
        List of Module names
    """
    return list(MODULE_METADATA.keys())


def get_persistent_modules() -> List[str]:
    """
    Get list of all persistent Module names

    Returns:
        List of persistent Module names
    """
    return [
        name for name, meta in MODULE_METADATA.items()
        if meta.get("instance_type") == "persistent"
    ]


def get_task_modules() -> List[str]:
    """
    Get list of all task-type Module names

    Returns:
        List of task-type Module names
    """
    return [
        name for name, meta in MODULE_METADATA.items()
        if meta.get("instance_type") == "task"
    ]
