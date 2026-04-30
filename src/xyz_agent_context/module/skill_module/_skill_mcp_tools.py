"""
@file_name: _skill_mcp_tools.py
@author: Bin Liang
@date: 2026-03-17
@description: SkillModule MCP Server tool definitions

Stateless MCP tools — each tool accepts agent_id + user_id and constructs
a temporary SkillModule instance to access the correct skills directory.
This follows the same pattern as ChatModule/JobModule MCP tools.

Tools:
- skill_save_config: Save an environment variable for a skill
- skill_list_required_env: Query a skill's required env vars and config status
- skill_save_study_summary: Save a structured study summary for a skill
"""

from loguru import logger
from mcp.server.fastmcp import FastMCP


def _get_skill_module(agent_id: str, user_id: str):
    """Create a temporary SkillModule instance for the given agent+user."""
    from xyz_agent_context.module.skill_module.skill_module import SkillModule
    return SkillModule(agent_id=agent_id, user_id=user_id)


def create_skill_mcp_server(port: int) -> FastMCP:
    """
    Create a SkillModule MCP Server instance.

    Args:
        port: MCP Server port

    Returns:
        FastMCP instance with all tools configured
    """
    mcp = FastMCP("skill_module")
    mcp.settings.port = port

    @mcp.tool()
    async def skill_save_config(
        agent_id: str,
        user_id: str,
        skill_name: str,
        env_key: str,
        env_value: str,
    ) -> str:
        """
        Save an environment variable for a skill.

        **WHEN TO CALL**: Every time you obtain a credential — API key, token, secret,
        account ID — you MUST call this tool. Even if you also saved it to a local file
        as the SKILL.md instructed. Without this call, the credential will NOT appear
        in the frontend config panel and will NOT be auto-injected at runtime.

        Common triggers:
        - You just registered on a platform and received an API key
        - A user gave you a key/token in conversation and asked you to configure it
        - You generated or rotated a credential during skill setup

        Args:
            agent_id: Your agent ID.
            user_id: The user ID.
            skill_name: Name of the skill (directory name under skills/).
            env_key: Environment variable name (e.g. "ARENA_API_KEY").
            env_value: The value to store (e.g. "arena_sk_xxxxx").

        Returns:
            Confirmation message.
        """
        try:
            sm = _get_skill_module(agent_id, user_id)
            sm.set_skill_env_config(skill_name, {env_key: env_value})

            logger.info(f"SkillMCP: Saved env {env_key} for skill '{skill_name}' (agent={agent_id})")
            return f"Successfully saved {env_key} for skill '{skill_name}'. It will be injected at runtime."

        except Exception as e:
            logger.exception(f"SkillMCP: Failed to save env config: {e}")
            return f"Failed to save config: {str(e)}"

    @mcp.tool()
    async def skill_list_required_env(
        agent_id: str,
        user_id: str,
        skill_name: str,
    ) -> str:
        """
        List the required environment variables for a skill and their configuration status.

        **WHEN TO CALL**: After completing registration or setup for a skill, call this
        to verify all required credentials are configured. Also useful when a user asks
        "what does this skill need?" or when diagnosing why a skill isn't working.

        Args:
            agent_id: Your agent ID.
            user_id: The user ID.
            skill_name: Name of the skill.

        Returns:
            A summary of required env vars with ✓/✗ status for each.
        """
        try:
            sm = _get_skill_module(agent_id, user_id)
            requirements = sm.get_skill_requirements(skill_name)
            env_config = sm.get_skill_env_config(skill_name)

            required_env = requirements.get("env", [])
            if not required_env:
                return f"Skill '{skill_name}' has no required environment variables."

            lines = [f"Required environment variables for '{skill_name}':"]
            for key in required_env:
                configured = key in env_config and env_config[key]
                status = "✓ configured" if configured else "✗ not configured"
                lines.append(f"  - {key}: {status}")

            return "\n".join(lines)

        except Exception as e:
            logger.exception(f"SkillMCP: Failed to list required env: {e}")
            return f"Failed to query: {str(e)}"

    @mcp.tool()
    async def skill_save_study_summary(
        agent_id: str,
        user_id: str,
        skill_name: str,
        summary: str,
    ) -> str:
        """
        Save a structured study summary for a skill.

        **WHEN TO CALL**: You MUST call this at the END of every skill study — it is
        the final required step. If you don't call this, the study will be marked as
        incomplete and the user will see a generic fallback message instead of your summary.

        The summary should be well-formatted Markdown covering:
        - What this skill does (core capabilities)
        - Any accounts/registrations you completed
        - Any credentials you saved (key names only, not values)
        - Any scheduled jobs you created (with their schedules and purposes)
        - Any pending actions that require human intervention (e.g., Twitter verification)

        This summary is displayed directly to the user in the Skills panel.
        Make it clear, useful, and well-structured.

        Args:
            agent_id: Your agent ID.
            user_id: The user ID.
            skill_name: Name of the skill.
            summary: Markdown-formatted study summary.

        Returns:
            Confirmation message.
        """
        try:
            sm = _get_skill_module(agent_id, user_id)
            sm.set_study_status(skill_name, "completed", result=summary)

            logger.info(f"SkillMCP: Saved study summary for '{skill_name}' ({len(summary)} chars)")
            return f"Study summary saved for skill '{skill_name}'."

        except Exception as e:
            logger.exception(f"SkillMCP: Failed to save study summary: {e}")
            return f"Failed to save summary: {str(e)}"

    return mcp
