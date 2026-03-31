"""
@file_name: step_display.py
@author: NetMind.AI
@date: 2025-12-24
@description: Step display configuration (developer-friendly)

Format step information into developer-friendly display format.
Preserve technical details for easier debugging and issue tracking.

Design principles:
1. Preserve technical terms: Narrative, Module, Instance, etc.
2. Display key IDs and parameters: for tracking and debugging
3. Structured data: for frontend rendering
"""

from __future__ import annotations

from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta


# =============================================================================
# Module human-friendly name mapping
# =============================================================================

MODULE_DISPLAY_CONFIG: Dict[str, Dict[str, str]] = {
    "SocialNetworkModule": {
        "icon": "👥",
        "name": "SocialNetwork",
        "desc": "Entity CRUD, relationship graph",
    },
    "JobModule": {
        "icon": "📋",
        "name": "Job",
        "desc": "Scheduled tasks, cron triggers",
    },
    "GeminiRAGModule": {
        "icon": "📚",
        "name": "GeminiRAG",
        "desc": "Vector search, document retrieval",
    },
    "AwarenessModule": {
        "icon": "🤖",
        "name": "Awareness",
        "desc": "Agent self-knowledge",
    },
    "ChatModule": {
        "icon": "💬",
        "name": "Chat",
        "desc": "Response generation",
    },
    "BasicInfoModule": {
        "icon": "ℹ️",
        "name": "BasicInfo",
        "desc": "Time, location utilities",
    },
}


# =============================================================================
# Tool call display configuration
# =============================================================================

TOOL_DISPLAY_CONFIG: Dict[str, Dict[str, str]] = {
    # Social Network tools
    "search_social_network": {
        "icon": "🔍",
        "name": "search_social_network",
        "desc_template": "keyword={search_keyword}",
    },
    "extract_entity_info": {
        "icon": "📝",
        "name": "extract_entity_info",
        "desc_template": "entity_id={entity_id}",
    },
    "get_contact_info": {
        "icon": "📇",
        "name": "get_contact_info",
        "desc_template": "entity_id={entity_id}",
    },

    # Job tools
    "job_create": {
        "icon": "➕",
        "name": "job_create",
        "desc_template": "title={title}",
    },
    "job_retrieval_semantic": {
        "icon": "🔍",
        "name": "job_retrieval_semantic",
        "desc_template": "query={query}",
    },

    # RAG tools
    "rag_query": {
        "icon": "📖",
        "name": "rag_query",
        "desc_template": "query={query}",
    },
    "rag_upload": {
        "icon": "📤",
        "name": "rag_upload",
        "desc_template": "file={file_name}",
    },

    # Chat tools
    "send_message_to_user_directly": {
        "icon": "💬",
        "name": "send_message_to_user_directly",
        "desc_template": "",
    },

    # Default
    "_default": {
        "icon": "🔧",
        "name": "",
        "desc_template": "",
    },
}


# =============================================================================
# Time formatting
# =============================================================================

def format_relative_time_cn(dt: Optional[datetime]) -> str:
    """
    Format time as a relative time string

    Args:
        dt: datetime object

    Returns:
        e.g., "just now", "5 minutes ago", "yesterday", "3 days ago"
    """
    if dt is None:
        return "unknown time"

    # Use UTC timezone uniformly
    from datetime import timezone
    now = datetime.now(timezone.utc)

    # If dt is offset-naive, assume it is UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    diff = now - dt

    if diff < timedelta(minutes=1):
        return "just now"
    elif diff < timedelta(hours=1):
        minutes = int(diff.total_seconds() / 60)
        return f"{minutes} minutes ago"
    elif diff < timedelta(days=1):
        hours = int(diff.total_seconds() / 3600)
        return f"{hours} hours ago"
    elif diff < timedelta(days=2):
        return "yesterday"
    elif diff < timedelta(days=7):
        days = diff.days
        return f"{days} days ago"
    else:
        return dt.strftime("%m/%d")


# =============================================================================
# Narrative formatting
# =============================================================================

def format_narrative_for_display(
    narratives: List[Any],
    scores: Optional[Dict[str, float]] = None
) -> Dict[str, Any]:
    """
    Format Narrative list into developer-friendly display format

    Args:
        narratives: List of Narrative objects
        scores: Optional score mapping {narrative_id: similarity_score}

    Returns:
        {
            "summary": "Selected 2 narratives",
            "items": [
                {"id": "nar_xxx", "name": "...", "time": "2h ago", "score": 0.85, "summary": "..."},
                ...
            ],
        }
    """
    if not narratives:
        return {
            "summary": "No matching narratives (new topic)",
            "items": [],
        }

    items = []
    for n in narratives[:5]:  # Display at most 5
        # Get narrative ID
        narrative_id = getattr(n, 'id', 'unknown')

        # Get narrative name
        name = "Untitled"
        if hasattr(n, 'narrative_info') and n.narrative_info:
            name = n.narrative_info.name or "Untitled"

        # Get time
        time_str = "unknown"
        if hasattr(n, 'updated_at') and n.updated_at:
            time_str = format_relative_time_cn(n.updated_at)

        # Get summary (first 60 characters)
        summary_text = ""
        if hasattr(n, 'narrative_info') and n.narrative_info:
            summary = n.narrative_info.current_summary or ""
            summary_text = summary[:60] + "..." if len(summary) > 60 else summary

        # Get score
        score = scores.get(narrative_id) if scores else None

        item = {
            "id": narrative_id,
            "name": name,
            "time": time_str,
            "summary": summary_text,
        }
        if score is not None:
            item["score"] = round(score, 3)

        items.append(item)

    count = len(narratives)
    summary = f"Selected {count} narratives"

    return {
        "summary": summary,
        "items": items,
    }


# =============================================================================
# Module/Instance formatting
# =============================================================================

def format_instances_for_display(instances: List[Any]) -> Dict[str, Any]:
    """
    Format Instance list into developer-friendly display format

    Automatically filters out cancelled and archived Instances.

    Args:
        instances: List of ModuleInstance objects

    Returns:
        {
            "summary": "Loaded 3 instances",
            "items": [
                {"icon": "👥", "instance_id": "social_xxx", "module": "SocialNetwork", "status": "active"},
                ...
            ],
        }
    """
    if not instances:
        return {
            "summary": "No modules loaded",
            "items": [],
        }

    items = []
    for inst in instances:
        # Filter out cancelled and archived Instances (do not display)
        status = "unknown"
        if hasattr(inst, 'status'):
            status = inst.status.value if hasattr(inst.status, 'value') else str(inst.status)
        if status in ("cancelled", "archived"):
            continue
        # Get instance_id
        instance_id = getattr(inst, 'instance_id', 'unknown')

        # Get module_class
        module_class = inst.module_class if hasattr(inst, 'module_class') else str(inst)

        # Get module display configuration
        config = MODULE_DISPLAY_CONFIG.get(module_class, {
            "icon": "🔌",
            "name": module_class.replace("Module", ""),
            "desc": "",
        })

        items.append({
            "icon": config["icon"],
            "instance_id": instance_id,
            "module": config["name"],
            "status": status,
            "desc": config["desc"],
        })

    count = len(items)
    summary = f"Loaded {count} instances"

    return {
        "summary": summary,
        "items": items,
    }


def format_execution_type_for_display(execution_type: str) -> Dict[str, str]:
    """
    Format execution type into display format

    Args:
        execution_type: "AGENT_LOOP" or "DIRECT_TRIGGER"

    Returns:
        {"icon": "🧠", "text": "AGENT_LOOP", "desc": "LLM reasoning required"}
    """
    if execution_type == "AGENT_LOOP":
        return {
            "icon": "🧠",
            "text": "AGENT_LOOP",
            "desc": "LLM reasoning required",
        }
    elif execution_type == "DIRECT_TRIGGER":
        return {
            "icon": "⚡",
            "text": "DIRECT_TRIGGER",
            "desc": "Direct tool execution",
        }
    else:
        return {
            "icon": "🔄",
            "text": execution_type,
            "desc": "Unknown execution type",
        }


# =============================================================================
# Tool call formatting
# =============================================================================

def format_tool_call_for_display(
    tool_name: str,
    arguments: Dict[str, Any],
    output: Optional[str] = None,
    is_completed: bool = False
) -> Dict[str, Any]:
    """
    Format tool call into developer-friendly display format

    Args:
        tool_name: Tool name (may contain mcp__ prefix)
        arguments: Tool arguments
        output: Tool output (if completed)
        is_completed: Whether completed

    Returns:
        {
            "icon": "🔍",
            "name": "search_social_network",
            "desc": "keyword=Alice",
            "status": "running" | "completed",
            "result_summary": "3 results"
        }
    """
    # Process MCP tool name (remove mcp__xxx__ prefix)
    short_name = tool_name
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__")
        if len(parts) >= 3:
            short_name = parts[-1]  # Take the last part

    # Get tool display configuration
    config = TOOL_DISPLAY_CONFIG.get(short_name, TOOL_DISPLAY_CONFIG["_default"])

    # Format description (display key parameters)
    desc = config.get("desc_template", "")
    if desc:
        try:
            desc = desc.format(**arguments)
        except (KeyError, ValueError):
            desc = ""

    # If no template or formatting failed, show first few parameters
    if not desc and arguments:
        param_strs = []
        for k, v in list(arguments.items())[:2]:  # Display at most 2 parameters
            if isinstance(v, str):
                v_display = v[:30] + "..." if len(v) > 30 else v
            else:
                v_display = str(v)[:30]
            param_strs.append(f"{k}={v_display}")
        desc = ", ".join(param_strs)

    result = {
        "icon": config["icon"],
        "name": config.get("name") or short_name,
        "desc": desc,
        "status": "completed" if is_completed else "running",
        "tool_name": tool_name,  # Keep full tool name
    }

    # If output exists, generate result summary
    if output and is_completed:
        result["result_summary"] = _summarize_tool_output(short_name, output)

    return result


def _summarize_tool_output(tool_name: str, output: str) -> str:
    """
    Generate a short summary of tool output (developer-friendly)
    """
    if not output:
        return "OK"

    # Try to parse JSON
    try:
        import json
        data = json.loads(output)

        # If it's a list, return element count
        if isinstance(data, list):
            return f"{len(data)} items"

        # If not a dict, return type info
        if not isinstance(data, dict):
            return f"type: {type(data).__name__}"

        # Generate different summaries based on tool type
        if "results" in data and isinstance(data["results"], list):
            count = len(data["results"])
            return f"{count} results"
        elif "count" in data:
            return f"{data['count']} results"
        elif "success" in data:
            if data["success"]:
                msg = data.get("message", "success")
                if not isinstance(msg, str):
                    msg = "success"
                return msg[:40] + "..." if len(msg) > 40 else msg
            else:
                msg = data.get("message", "failed")
                if not isinstance(msg, str):
                    msg = "failed"
                return f"FAILED: {msg[:30]}"
        elif "entity" in data:
            entity_id = data.get("entity", {}).get("entity_id", "")
            return f"entity: {entity_id}" if entity_id else "entity retrieved"
        elif "job_id" in data:
            return f"job_id: {data['job_id']}"
        else:
            # Return the list of JSON keys
            keys = list(data.keys())[:3]
            return f"keys: {keys}"

    except (json.JSONDecodeError, TypeError):
        # Not JSON, return truncated text
        if len(output) > 60:
            return output[:57] + "..."
        return output


# =============================================================================
# Thinking formatting
# =============================================================================

def format_thinking_for_display(thinking_content: str) -> Dict[str, Any]:
    """
    Format AI thinking content into developer-friendly display

    Args:
        thinking_content: Raw thinking content

    Returns:
        {
            "length": 1234,
            "preview": "First 100 chars...",
            "full_content": "..."
        }
    """
    if not thinking_content:
        return {
            "length": 0,
            "preview": "",
            "full_content": "",
        }

    # Extract first 100 characters as preview
    preview = thinking_content[:100]
    if len(thinking_content) > 100:
        preview += "..."

    return {
        "length": len(thinking_content),
        "preview": preview,
        "full_content": thinking_content,
    }
