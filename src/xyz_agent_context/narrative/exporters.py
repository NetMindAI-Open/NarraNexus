"""
@file_name: exporters.py
@author: NetMind.AI
@date: 2025-12-22
@description: Data export utilities for the Narrative module

Merged from:
- narrative_markdown_manager.py: Markdown file management
- trajectory_recorder.py: Execution trajectory recording

Feature categories:
1. NarrativeMarkdownManager: Markdown file management for LLM decision context
2. TrajectoryRecorder: Execution trajectory recording for debugging and analysis

File structure:
    {base_path}/{agent_id}/{user_id}/
        ├── narratives/
        │   ├── {narrative_id}.md          # Narrative main file
        │   └── {narrative_id}_stats.json  # Statistics
        └── trajectories/
            └── {narrative_id}/
                ├── round_001.json
                ├── round_002.json
                └── index.json
"""

from __future__ import annotations

import os
import json
from typing import List, Dict, Any, Optional, TYPE_CHECKING
from datetime import datetime
from loguru import logger

if TYPE_CHECKING:
    from .models import Narrative
    from xyz_agent_context.agent_runtime.execution_state import ExecutionState
    from xyz_agent_context.schema.module_schema import ModuleInstance


# =============================================================================
# NarrativeMarkdownManager - Markdown File Management
# =============================================================================

class NarrativeMarkdownManager:
    """
    Narrative Markdown File Manager

    Responsible for managing Narrative-related Markdown files, used for:
    1. Persistently recording Instance states and relationship graphs
    2. Providing historical context for LLM decisions
    3. Recording statistics

    Usage:
        >>> manager = NarrativeMarkdownManager(agent_id, user_id)
        >>> await manager.initialize_markdown(narrative)
        >>> history = await manager.read_markdown(narrative.id)
        >>> await manager.update_instances(narrative, instances, relationship_graph, changes_summary)
    """

    def __init__(self, agent_id: str, user_id: str, base_path: Optional[str] = None):
        """
        Initialize NarrativeMarkdownManager

        Args:
            agent_id: Agent unique identifier
            user_id: User unique identifier
            base_path: Base path, defaults to environment variable NARRATIVE_MARKDOWN_PATH or ~/.nexusagent/data/narratives
        """
        self.agent_id = agent_id
        self.user_id = user_id
        from xyz_agent_context.settings import settings
        self.base_path = base_path or settings.narrative_markdown_path

        # Build directory path
        self.narratives_dir = os.path.join(self.base_path, agent_id, user_id, "narratives")

        logger.debug(f"NarrativeMarkdownManager initialized: {self.narratives_dir}")

    def _get_markdown_path(self, narrative_id: str) -> str:
        """Get the Narrative Markdown file path"""
        return os.path.join(self.narratives_dir, f"{narrative_id}.md")

    def _get_stats_path(self, narrative_id: str) -> str:
        """Get the Narrative statistics file path"""
        return os.path.join(self.narratives_dir, f"{narrative_id}_stats.json")

    def _ensure_dir_exists(self) -> None:
        """Ensure the directory exists"""
        if not os.path.exists(self.narratives_dir):
            os.makedirs(self.narratives_dir, exist_ok=True)
            logger.info(f"Created narratives directory: {self.narratives_dir}")

    async def initialize_markdown(self, narrative: "Narrative") -> None:
        """
        Initialize the Narrative's Markdown file (if it does not exist)

        Creates a new Markdown file containing the Narrative's basic information.

        Args:
            narrative: Narrative object
        """
        self._ensure_dir_exists()
        md_path = self._get_markdown_path(narrative.id)

        # Skip initialization if file already exists
        if os.path.exists(md_path):
            logger.debug(f"Markdown file already exists: {md_path}")
            return

        # Build initial Markdown content
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        content = f"""# Narrative: {narrative.narrative_info.name}

> **ID**: `{narrative.id}`
> **Type**: `{narrative.type.value}`
> **Agent**: `{narrative.agent_id}`
> **Created**: {now}

## 📝 Description

{narrative.narrative_info.description}

## 📊 Current Summary

{narrative.narrative_info.current_summary or "_No summary yet_"}

---

## 🧩 Active Instances

_No instances yet_

---

## 🔗 Relationship Graph

_No relationship graph yet_

---

## 📈 Statistics

| Metric | Value |
|--------|-------|
| Total Rounds | 0 |
| Total Tool Calls | 0 |
| Instance Changes | 0 |

---

## 📜 Change History

_No changes recorded yet_

"""

        # Write file
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"Initialized Markdown file: {md_path}")

    async def read_markdown(self, narrative_id: str) -> str:
        """
        Read the Markdown content of a Narrative

        Args:
            narrative_id: Narrative ID

        Returns:
            Markdown file content, or empty string if the file does not exist
        """
        md_path = self._get_markdown_path(narrative_id)

        if not os.path.exists(md_path):
            logger.warning(f"Markdown file not found: {md_path}")
            return ""

        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read()

        logger.debug(f"Read Markdown file: {md_path} ({len(content)} chars)")
        return content

    async def update_instances(
        self,
        narrative: "Narrative",
        instances: List["ModuleInstance"],
        relationship_graph: str,
        changes_summary: Dict[str, Any]
    ) -> None:
        """
        Update Instances and relationship graph in Markdown

        Args:
            narrative: Narrative object
            instances: List of currently active Module Instances
            relationship_graph: Relationship graph (Mermaid format or text description)
            changes_summary: Change summary containing added, removed, updated lists
        """
        md_path = self._get_markdown_path(narrative.id)
        content = await self.read_markdown(narrative.id)

        if not content:
            # If file does not exist, initialize first
            await self.initialize_markdown(narrative)
            content = await self.read_markdown(narrative.id)

        # Build Instances section
        instances_section = self._build_instances_section(instances)

        # Build relationship graph section
        graph_section = self._build_relationship_graph_section(relationship_graph)

        # Build change history entry
        change_entry = self._build_change_entry(changes_summary)

        # Update Markdown content
        content = self._update_section(content, "## 🧩 Active Instances", instances_section)
        content = self._update_section(content, "## 🔗 Relationship Graph", graph_section)
        content = self._append_to_section(content, "## 📜 Change History", change_entry)

        # Write file
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"Updated Markdown instances: {md_path}")

    async def update_statistics(self, narrative_id: str, stats: Dict[str, Any]) -> None:
        """
        Update statistics in Markdown

        Args:
            narrative_id: Narrative ID
            stats: Statistics dictionary
        """
        md_path = self._get_markdown_path(narrative_id)
        content = await self.read_markdown(narrative_id)

        if not content:
            logger.warning(f"Cannot update statistics: Markdown file not found for {narrative_id}")
            return

        # Build statistics section
        stats_section = self._build_statistics_section(stats)

        # Update Markdown content
        content = self._update_section(content, "## 📈 Statistics", stats_section)

        # Write file
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(content)

        # Also save statistics in JSON format (for programmatic reading)
        stats_path = self._get_stats_path(narrative_id)
        stats_with_timestamp = {
            **stats,
            "updated_at": datetime.now().isoformat()
        }
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(stats_with_timestamp, f, indent=2, ensure_ascii=False)

        logger.info(f"Updated Markdown statistics: {md_path}")

    def _build_instances_section(self, instances: List["ModuleInstance"]) -> str:
        """Build the Markdown content for the Instances section"""
        if not instances:
            return "_No active instances_\n"

        lines = []
        for inst in instances:
            status_value = inst.status.value if hasattr(inst.status, 'value') else str(inst.status)
            status_emoji = {
                "active": "🟢",
                "in_progress": "🔵",
                "blocked": "🟡",
                "completed": "✅",
                "failed": "❌"
            }.get(status_value, "⚪")

            lines.append(f"### {status_emoji} `{inst.instance_id}`")
            lines.append(f"- **Module**: `{inst.module_class}`")
            lines.append(f"- **Status**: `{status_value}`")
            if inst.description:
                lines.append(f"- **Description**: {inst.description}")
            if inst.dependencies:
                deps = ", ".join([f"`{d}`" for d in inst.dependencies])
                lines.append(f"- **Dependencies**: {deps}")
            lines.append("")

        return "\n".join(lines)

    def _build_relationship_graph_section(self, relationship_graph: str) -> str:
        """Build the Markdown content for the relationship graph section"""
        if not relationship_graph:
            return "_No relationship graph_\n"

        # If in Mermaid format, add code block
        if "graph" in relationship_graph.lower() or "flowchart" in relationship_graph.lower():
            return f"```mermaid\n{relationship_graph}\n```\n"
        else:
            return f"{relationship_graph}\n"

    def _build_change_entry(self, changes_summary: Dict[str, Any]) -> str:
        """Build a change history entry"""
        if not changes_summary:
            return ""

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [f"\n### {now}\n"]

        added = changes_summary.get("added", [])
        removed = changes_summary.get("removed", [])
        updated = changes_summary.get("updated", [])

        if added:
            lines.append(f"- ➕ Added: {', '.join([f'`{a}`' for a in added])}")
        if removed:
            lines.append(f"- ➖ Removed: {', '.join([f'`{r}`' for r in removed])}")
        if updated:
            lines.append(f"- 🔄 Updated: {', '.join([f'`{u}`' for u in updated])}")

        if not (added or removed or updated):
            lines.append("- _No changes_")

        lines.append("")
        return "\n".join(lines)

    def _build_statistics_section(self, stats: Dict[str, Any]) -> str:
        """Build the Markdown content for the statistics section"""
        lines = [
            "| Metric | Value |",
            "|--------|-------|",
            f"| Total Rounds | {stats.get('total_rounds', 0)} |",
            f"| Total Tool Calls | {stats.get('total_toolcalls', 0)} |",
            f"| Instance Changes | {stats.get('instance_changes', 0)} |",
            f"| Avg Active Instances | {stats.get('avg_active_instances', 0)} |",
            f"| Avg Tool Calls/Round | {stats.get('avg_toolcalls_per_round', 0):.1f} |",
            f"| Most Used Module | `{stats.get('most_used_module', 'N/A')}` |",
            ""
        ]
        return "\n".join(lines)

    def _update_section(self, content: str, section_header: str, new_content: str) -> str:
        """
        Update the content of a specified section in Markdown

        Args:
            content: Original Markdown content
            section_header: Section header (e.g., "## Active Instances")
            new_content: New section content

        Returns:
            Updated Markdown content
        """
        lines = content.split("\n")
        result = []
        in_section = False
        section_replaced = False

        for i, line in enumerate(lines):
            if line.strip() == section_header:
                # Found target section
                result.append(line)
                result.append("")
                result.append(new_content.rstrip())
                in_section = True
                section_replaced = True
            elif in_section and line.startswith("## "):
                # Encountered next section, stop replacement
                in_section = False
                result.append("---")
                result.append("")
                result.append(line)
            elif in_section and line.strip() == "---":
                # Skip original separator
                continue
            elif not in_section:
                result.append(line)

        if not section_replaced:
            # If section not found, append at the end
            result.append("")
            result.append(section_header)
            result.append("")
            result.append(new_content.rstrip())
            result.append("")
            result.append("---")

        return "\n".join(result)

    def _append_to_section(self, content: str, section_header: str, new_entry: str) -> str:
        """
        Append content to a specified section

        Args:
            content: Original Markdown content
            section_header: Section header
            new_entry: Content to append

        Returns:
            Updated Markdown content
        """
        if not new_entry.strip():
            return content

        lines = content.split("\n")
        result = []
        section_found = False

        for i, line in enumerate(lines):
            result.append(line)
            if line.strip() == section_header:
                section_found = True
                # Add new entry after section header
                result.append("")
                result.append(new_entry.rstrip())

        if not section_found:
            # If section not found, append at the end
            result.append("")
            result.append(section_header)
            result.append("")
            result.append(new_entry.rstrip())

        return "\n".join(result)


# =============================================================================
# TrajectoryRecorder - Execution Trajectory Recording
# =============================================================================

class TrajectoryRecorder:
    """
    Trajectory Recorder

    Responsible for recording the complete trajectory of each Agent execution round, used for:
    1. Debugging and issue tracing
    2. Analyzing Agent behavior patterns
    3. Optimizing decision strategies

    Usage:
        >>> recorder = TrajectoryRecorder(agent_id, user_id)
        >>> await recorder.record_round(
        ...     narrative_id=narrative.id,
        ...     round_num=1,
        ...     user_input="Hello",
        ...     instances=active_instances,
        ...     relationship_graph="...",
        ...     execution_state=state,
        ...     execution_path="AGENT_LOOP",
        ...     reasoning="...",
        ...     changes_summary={...},
        ...     previous_instances=prev_instances
        ... )
    """

    def __init__(self, agent_id: str, user_id: str, base_path: Optional[str] = None):
        """
        Initialize TrajectoryRecorder

        Args:
            agent_id: Agent unique identifier
            user_id: User unique identifier
            base_path: Base path, defaults to environment variable TRAJECTORY_PATH or ~/.nexusagent/data/trajectories
        """
        self.agent_id = agent_id
        self.user_id = user_id
        from xyz_agent_context.settings import settings
        self.base_path = base_path or settings.trajectory_path

        # Build directory path
        self.trajectories_dir = os.path.join(self.base_path, agent_id, user_id, "trajectories")

        logger.debug(f"TrajectoryRecorder initialized: {self.trajectories_dir}")

    def _get_narrative_dir(self, narrative_id: str) -> str:
        """Get the Trajectory directory path for a Narrative"""
        return os.path.join(self.trajectories_dir, narrative_id)

    def _get_round_path(self, narrative_id: str, round_num: int) -> str:
        """Get the Trajectory file path for a single round"""
        narrative_dir = self._get_narrative_dir(narrative_id)
        return os.path.join(narrative_dir, f"round_{round_num:03d}.json")

    def _ensure_dir_exists(self, narrative_id: str) -> None:
        """Ensure the directory exists"""
        narrative_dir = self._get_narrative_dir(narrative_id)
        if not os.path.exists(narrative_dir):
            os.makedirs(narrative_dir, exist_ok=True)
            logger.info(f"Created trajectory directory: {narrative_dir}")

    async def record_round(
        self,
        narrative_id: str,
        round_num: int,
        user_input: str,
        instances: List["ModuleInstance"],
        relationship_graph: str,
        execution_state: "ExecutionState",
        execution_path: str,
        reasoning: str,
        changes_summary: Dict[str, Any],
        previous_instances: List["ModuleInstance"]
    ) -> None:
        """
        Record the complete trajectory of one execution round

        Args:
            narrative_id: Narrative ID
            round_num: Round number
            user_input: User input content
            instances: List of currently active Module Instances
            relationship_graph: Relationship graph
            execution_state: Execution state object
            execution_path: Execution path (AGENT_LOOP or DIRECT_TRIGGER)
            reasoning: LLM decision reasoning
            changes_summary: Instance change summary
            previous_instances: Instance list before the decision
        """
        self._ensure_dir_exists(narrative_id)
        round_path = self._get_round_path(narrative_id, round_num)

        # Build Trajectory data
        trajectory_data = {
            # ===== Metadata =====
            "meta": {
                "narrative_id": narrative_id,
                "round_num": round_num,
                "agent_id": self.agent_id,
                "user_id": self.user_id,
                "execution_path": execution_path,
                "recorded_at": datetime.now().isoformat()
            },

            # ===== Input =====
            "input": {
                "user_input": user_input,
                "input_length": len(user_input)
            },

            # ===== Pre-decision State =====
            "before_decision": {
                "instances": self._serialize_instances(previous_instances),
                "instance_count": len(previous_instances)
            },

            # ===== Decision Process =====
            "decision": {
                "reasoning": reasoning,
                "changes_summary": changes_summary,
                "added": changes_summary.get("added", []),
                "removed": changes_summary.get("removed", []),
                "updated": changes_summary.get("updated", [])
            },

            # ===== Post-decision State =====
            "after_decision": {
                "instances": self._serialize_instances(instances),
                "instance_count": len(instances),
                "relationship_graph": relationship_graph
            },

            # ===== Execution Result =====
            "execution": {
                "final_output": execution_state.final_output,
                "output_length": len(execution_state.final_output),
                "response_count": execution_state.response_count,
                "tool_call_count": execution_state.tool_call_count,
                "thinking_count": execution_state.thinking_count,
                "all_steps": list(execution_state.all_steps) if hasattr(execution_state, 'all_steps') else []
            },

            # ===== Statistics Summary =====
            "summary": {
                "instance_delta": len(instances) - len(previous_instances),
                "has_changes": bool(changes_summary.get("added") or changes_summary.get("removed") or changes_summary.get("updated")),
                "total_steps": len(execution_state.all_steps) if hasattr(execution_state, 'all_steps') else 0
            }
        }

        # Write file
        with open(round_path, "w", encoding="utf-8") as f:
            json.dump(trajectory_data, f, indent=2, ensure_ascii=False, default=str)

        logger.info(f"Recorded trajectory: {round_path}")

        # Update index
        await self._update_index(narrative_id, round_num, trajectory_data["summary"])

    def _serialize_instances(self, instances: List["ModuleInstance"]) -> List[Dict[str, Any]]:
        """Serialize the Instance list"""
        result = []
        for inst in instances:
            status_value = inst.status.value if hasattr(inst.status, 'value') else str(inst.status)
            result.append({
                "instance_id": inst.instance_id,
                "module_class": inst.module_class,
                "description": inst.description,
                "status": status_value,
                "dependencies": inst.dependencies,
                "created_at": inst.created_at.isoformat() if inst.created_at else None,
                "last_used_at": inst.last_used_at.isoformat() if inst.last_used_at else None
            })
        return result

    async def _update_index(self, narrative_id: str, round_num: int, summary: Dict[str, Any]) -> None:
        """Update the Trajectory index file"""
        index_path = os.path.join(self._get_narrative_dir(narrative_id), "index.json")

        # Read existing index
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                index_data = json.load(f)
        else:
            index_data = {
                "narrative_id": narrative_id,
                "agent_id": self.agent_id,
                "user_id": self.user_id,
                "rounds": [],
                "created_at": datetime.now().isoformat()
            }

        # Add new round record
        round_entry = {
            "round_num": round_num,
            "recorded_at": datetime.now().isoformat(),
            "summary": summary
        }

        # Check if this round already exists (avoid duplicates)
        existing_rounds = {r["round_num"] for r in index_data["rounds"]}
        if round_num not in existing_rounds:
            index_data["rounds"].append(round_entry)
            index_data["rounds"].sort(key=lambda x: x["round_num"])

        index_data["updated_at"] = datetime.now().isoformat()
        index_data["total_rounds"] = len(index_data["rounds"])

        # Write index file
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index_data, f, indent=2, ensure_ascii=False)

        logger.debug(f"Updated trajectory index: {index_path}")

    async def get_round(self, narrative_id: str, round_num: int) -> Optional[Dict[str, Any]]:
        """Get Trajectory data for a specified round"""
        round_path = self._get_round_path(narrative_id, round_num)

        if not os.path.exists(round_path):
            logger.warning(f"Trajectory not found: {round_path}")
            return None

        with open(round_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return data

    async def get_all_rounds(self, narrative_id: str) -> List[Dict[str, Any]]:
        """Get all Trajectory data for a specified Narrative"""
        narrative_dir = self._get_narrative_dir(narrative_id)

        if not os.path.exists(narrative_dir):
            logger.warning(f"Trajectory directory not found: {narrative_dir}")
            return []

        rounds = []
        for filename in os.listdir(narrative_dir):
            if filename.startswith("round_") and filename.endswith(".json"):
                file_path = os.path.join(narrative_dir, filename)
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    rounds.append(data)

        # Sort by round_num
        rounds.sort(key=lambda x: x["meta"]["round_num"])

        return rounds

    async def get_latest_round(self, narrative_id: str) -> Optional[Dict[str, Any]]:
        """Get the latest round of Trajectory data"""
        index_path = os.path.join(self._get_narrative_dir(narrative_id), "index.json")

        if not os.path.exists(index_path):
            # No index, try scanning the directory
            all_rounds = await self.get_all_rounds(narrative_id)
            return all_rounds[-1] if all_rounds else None

        with open(index_path, "r", encoding="utf-8") as f:
            index_data = json.load(f)

        if not index_data.get("rounds"):
            return None

        latest_round_num = index_data["rounds"][-1]["round_num"]
        return await self.get_round(narrative_id, latest_round_num)

    async def get_statistics(self, narrative_id: str) -> Dict[str, Any]:
        """Get Trajectory statistics"""
        all_rounds = await self.get_all_rounds(narrative_id)

        if not all_rounds:
            return {
                "total_rounds": 0,
                "total_tool_calls": 0,
                "total_thinking": 0,
                "avg_output_length": 0,
                "total_instance_changes": 0
            }

        total_tool_calls = sum(r["execution"]["tool_call_count"] for r in all_rounds)
        total_thinking = sum(r["execution"]["thinking_count"] for r in all_rounds)
        total_output_length = sum(r["execution"]["output_length"] for r in all_rounds)
        total_changes = sum(1 for r in all_rounds if r["summary"]["has_changes"])

        return {
            "total_rounds": len(all_rounds),
            "total_tool_calls": total_tool_calls,
            "total_thinking": total_thinking,
            "avg_output_length": total_output_length / len(all_rounds) if all_rounds else 0,
            "avg_tool_calls_per_round": total_tool_calls / len(all_rounds) if all_rounds else 0,
            "total_instance_changes": total_changes,
            "change_rate": total_changes / len(all_rounds) if all_rounds else 0
        }
