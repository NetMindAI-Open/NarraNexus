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

    # =====================================================================
    # Subproject 2 (Bundle Export/Import): Skill backup tools
    # =====================================================================

    @mcp.tool()
    async def skill_backup_from_github(
        agent_id: str,
        user_id: str,
        skill_name: str,
        github_url: str,
        branch: str = "main",
    ) -> str:
        """
        Back up a skill that you installed by cloning/downloading from GitHub.

        **WHEN TO CALL**: Right after you successfully install a skill from a GitHub
        URL outside the official install API (e.g., you ran `git clone` or downloaded
        a tarball yourself into the skills/ directory). Without this call, when the
        user later exports a bundle, this skill will appear as "unbacked-up" — they'll
        need to manually fill the URL or upload a zip.

        Args:
            agent_id: Your agent ID.
            user_id: The user ID.
            skill_name: Skill directory name under skills/.
            github_url: The GitHub repo URL (https://github.com/owner/repo).
            branch: Branch name; defaults to "main".

        Returns:
            Confirmation with archive location.
        """
        try:
            from xyz_agent_context.bundle.skill_backup import (
                archive_github_tarball,
                register_archive,
            )
            archive_path, sha = await archive_github_tarball(
                user_id=user_id,
                skill_name=skill_name,
                github_url=github_url,
                branch=branch,
            )
            await register_archive(
                user_id=user_id,
                skill_name=skill_name,
                source_type="github",
                source_url=github_url,
                archive_path=str(archive_path),
                sha256=sha,
            )
            return (
                f"Backed up '{skill_name}' from GitHub. Archive: {archive_path.name} (sha256={sha[:8]}…)."
            )
        except Exception as e:
            logger.exception(f"skill_backup_from_github failed: {e}")
            return f"Backup failed: {e}"

    @mcp.tool()
    async def skill_backup_from_md(
        agent_id: str,
        user_id: str,
        skill_name: str,
        skill_md_content: str,
    ) -> str:
        """
        Back up a skill that came as a single SKILL.md file (no zip, no GitHub).

        **WHEN TO CALL**: After you install a skill that has only a SKILL.md (e.g., the
        user pasted a SKILL.md into your workspace, or you downloaded a single .md file).
        We package the SKILL.md content into a zip and register it as the archive.

        Args:
            agent_id: Your agent ID.
            user_id: The user ID.
            skill_name: Skill directory name.
            skill_md_content: Full SKILL.md content (Markdown text).

        Returns:
            Confirmation message.
        """
        try:
            from xyz_agent_context.bundle.skill_backup import (
                archive_md_only,
                register_archive,
            )
            archive_path, sha = await archive_md_only(
                user_id=user_id,
                skill_name=skill_name,
                skill_md_content=skill_md_content,
            )
            await register_archive(
                user_id=user_id,
                skill_name=skill_name,
                source_type="zip",
                source_url=None,
                archive_path=str(archive_path),
                sha256=sha,
            )
            return f"Backed up '{skill_name}' as md-only zip (sha256={sha[:8]}…)."
        except Exception as e:
            logger.exception(f"skill_backup_from_md failed: {e}")
            return f"Backup failed: {e}"

    @mcp.tool()
    async def skill_backup_from_local_zip(
        agent_id: str,
        user_id: str,
        skill_name: str,
        zip_file_path: str,
    ) -> str:
        """
        Back up a skill using a zip file already in the agent's workspace.

        **WHEN TO CALL**: When a complete skill zip exists under your workspace (e.g.,
        you downloaded one earlier) and you want to register it as the source for the
        installed skill. The zip file path MUST be inside your workspace; absolute
        paths or paths outside workspace are rejected to prevent escalation.

        Args:
            agent_id: Your agent ID.
            user_id: The user ID.
            skill_name: Skill directory name.
            zip_file_path: Path inside your workspace pointing at the zip.

        Returns:
            Confirmation message.
        """
        try:
            from xyz_agent_context.bundle.skill_backup import (
                archive_local_zip,
                register_archive,
            )
            archive_path, sha = await archive_local_zip(
                user_id=user_id,
                agent_id=agent_id,
                skill_name=skill_name,
                zip_file_path=zip_file_path,
            )
            await register_archive(
                user_id=user_id,
                skill_name=skill_name,
                source_type="zip",
                source_url=None,
                archive_path=str(archive_path),
                sha256=sha,
            )
            return f"Backed up '{skill_name}' from local zip (sha256={sha[:8]}…)."
        except Exception as e:
            logger.exception(f"skill_backup_from_local_zip failed: {e}")
            return f"Backup failed: {e}"

    @mcp.tool()
    async def skill_list_unbackedup(
        agent_id: str,
        user_id: str,
    ) -> str:
        """
        List skills installed in this agent that have NO archive backup yet.

        **WHEN TO CALL**: Proactively before the user asks to export a bundle, OR
        right after you finish installing a new skill, OR whenever the user mentions
        backing up / sharing skills.

        Returns:
            A list of skill names without backups; empty list means everything's good.
        """
        try:
            from xyz_agent_context.bundle.skill_backup import list_unbackedup
            skills = await list_unbackedup(user_id=user_id, agent_id=agent_id)
            if not skills:
                return "All installed skills have an archive — you're good."
            return (
                "These skills have no archive (won't be exportable as URL/Zip without manual fill):\n  - "
                + "\n  - ".join(skills)
            )
        except Exception as e:
            logger.exception(f"skill_list_unbackedup failed: {e}")
            return f"Query failed: {e}"

    @mcp.tool()
    async def skill_search_marketplace(
        agent_id: str,
        user_id: str,
        query: str,
        capability: str = "",
    ) -> str:
        """
        Search the NarraNexus Skill Marketplace for installable skills.

        **WHEN TO CALL**: The user asks for a capability you don't have, or asks
        you to find/install a skill. After reviewing the results, install the
        chosen one with `skill_install`.

        Args:
            agent_id: Agent ID
            user_id: User ID
            query: Free-text search (name/description/tags)
            capability: Optional capability tag filter, e.g. "search:web"

        Returns:
            A ranked list of matching skills with id, version, description and
            security-scan status, or a message when nothing matches.
        """
        try:
            from xyz_agent_context.skill_marketplace_service import SkillMarketplaceService

            payload = await SkillMarketplaceService().search(
                q=query or None,
                capability=capability or None,
                agent_id=agent_id,
                user_id=user_id,
                limit=10,
            )
            items = payload.get("items", [])
            if not items:
                return f"No marketplace skills match '{query}'."
            lines = []
            for item in items:
                skill_id = item.get("skill_id") or item.get("id")
                flags = []
                if item.get("installed"):
                    flags.append("installed")
                if item.get("update_available"):
                    flags.append("update available")
                suffix = f" [{', '.join(flags)}]" if flags else ""
                scan = (item.get("security_scan") or {}).get("status") or item.get("scan_status")
                lines.append(
                    f"- {skill_id}@{item.get('version')}{suffix} — "
                    f"{item.get('description') or ''} (scan: {scan}, "
                    f"downloads: {item.get('downloads', 0)})"
                )
            return "Marketplace results:\n" + "\n".join(lines)
        except Exception as e:
            logger.exception(f"skill_search_marketplace failed: {e}")
            return f"Marketplace search is unavailable right now: {e}"

    @mcp.tool()
    async def skill_install(
        agent_id: str,
        user_id: str,
        skill_id_or_url: str,
        version: str = "",
    ) -> str:
        """
        Install a skill for this agent — the ONLY sanctioned way to install.

        Never create skill directories by hand under `skills/`. This tool runs
        the full install pipeline (security scan, conflict handling with config
        migration, audit trail). Accepts either a marketplace skill id (from
        `skill_search_marketplace`) or a GitHub URL the user provided.

        Args:
            agent_id: Agent ID
            user_id: User ID
            skill_id_or_url: Marketplace skill id, or https://github.com/... URL
            version: Optional exact version (marketplace source only)

        Returns:
            Outcome message. If configuration is required, it says so — tell the
            user to open the Skill tab and fill in the required keys.
        """
        try:
            from xyz_agent_context.skill_marketplace_service import SkillMarketplaceService

            service = SkillMarketplaceService()
            if skill_id_or_url.startswith(("http://", "https://", "github:")):
                result = await service.install_from_url(agent_id, user_id, skill_id_or_url)
            else:
                result = await service.install(
                    agent_id, user_id, skill_id_or_url, version=version or None
                )
            name = result.skill.name if result.skill else skill_id_or_url
            if result.status == "already_installed":
                return f"Skill '{name}' is already installed at this version."
            message = f"Installed skill '{name}'"
            if result.replaced_version:
                message += f" (replaced v{result.replaced_version}; existing config migrated)"
            message += ". It takes effect on the next run."
            if result.config_required:
                message += (
                    " ⚠️ It needs configuration — tell the user to open the Skill tab "
                    "and fill in the required keys before using it."
                )
            if result.warnings:
                message += f" Note: {len(result.warnings)} low-risk security warning(s) were found."
            return message
        except FileNotFoundError:
            return f"Skill '{skill_id_or_url}' was not found in the marketplace."
        except ValueError as e:
            return f"Install rejected: {e}"
        except Exception as e:
            logger.exception(f"skill_install failed: {e}")
            return f"Install failed: {e}"

    @mcp.tool()
    async def skill_uninstall(agent_id: str, user_id: str, skill_name: str) -> str:
        """
        Uninstall a skill — the ONLY sanctioned way to remove one.

        Never delete skill directories by hand under `skills/`. Built-in skills
        cannot be removed (disable them instead via the UI).

        Args:
            agent_id: Agent ID
            user_id: User ID
            skill_name: The installed skill's name (directory name)

        Returns:
            Outcome message.
        """
        try:
            from xyz_agent_context.skill_marketplace_service import SkillMarketplaceService

            removed = await SkillMarketplaceService().uninstall(agent_id, user_id, skill_name)
            if removed:
                return f"Skill '{skill_name}' has been uninstalled."
            return f"Skill '{skill_name}' is not installed."
        except ValueError as e:
            return f"Cannot uninstall: {e}"
        except Exception as e:
            logger.exception(f"skill_uninstall failed: {e}")
            return f"Uninstall failed: {e}"

    return mcp
