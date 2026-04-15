"""
@file_name: skill_module.py
@author: NetMind.AI
@date: 2026-02-03
@description: Skill Module - Manages user Skills

Skill Module manages Skills under the user's workspace:
- MCP tools for agent self-configuration (env vars, study summary)
- No database: State is expressed through the filesystem
- Always loaded: No intelligent decision-making or Instance records needed

Skills directory:
- Located at {agent_workspace}/{agent_id}_{user_id}/skills/
- Same as Claude Agent's cwd

MCP Tools:
- skill_save_config: Save env var for a skill (credentials auto-injected at runtime)
- skill_list_required_env: Query required env vars and config status
- skill_save_study_summary: Save structured Markdown study summary
"""

import os
import shutil
import tempfile
import subprocess
import zipfile
import json
from pathlib import Path
from typing import Optional, List
from datetime import datetime
from urllib.parse import urlparse

import yaml
from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule, mcp_host
from xyz_agent_context.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
)
from xyz_agent_context.schema.skill_schema import SkillInfo
from xyz_agent_context.utils import DatabaseClient
from xyz_agent_context.utils.file_safety import (
    ensure_within_directory,
    sanitize_filename,
    validate_zip_member_path,
)


# =============================================================================
# Shared prompt constants
# WORKSPACE_RULES is shared between instructions (conversation) and study prompt
# =============================================================================

WORKSPACE_RULES = (
    "- All files you create or download MUST be stored inside your workspace "
    "(`skills/<skill-name>/` or current working directory)\n"
    "- When a SKILL.md suggests paths like `~/.foo/` or `~/.config/foo/`, "
    "**remap them** to `skills/<skill-name>/` instead\n"
    "- The ONLY exception: system-level tools that genuinely require global installation "
    "(e.g., `pip install`)\n"
    "- **Credentials and API keys** — do BOTH of the following:\n"
    "  1. If the SKILL.md instructs you to save to a local file (e.g., `credentials.json`), "
    "do so inside `skills/<skill-name>/` (remapped path)\n"
    "  2. **Also** call `skill_save_config` for each key — this registers it in the system "
    "so it appears in the frontend config panel and is auto-injected at runtime"
)

SKILL_INSTRUCTIONS_TEMPLATE = """\
#### Available Skills

Your skills directory: `skills/` (relative to your current working directory)

{skills_table}

##### 1. Using Skills
- When a task matches a Skill, read its `SKILL.md` using `cat`
- Follow the instructions; access referenced files (guides, scripts) as needed
- For scripts, execute them and use the output (don't read the source code)

##### 2. Workspace & File Storage Rules
""" + WORKSPACE_RULES + """

##### 3. Skill Configuration Tools
| Tool | Purpose |
|------|---------|
| `skill_save_config(agent_id, user_id, skill_name, env_key, env_value)` | Save a credential for a skill |
| `skill_list_required_env(agent_id, user_id, skill_name)` | Check required env vars and their status |
| `skill_save_study_summary(agent_id, user_id, skill_name, summary)` | Save a Markdown study summary |

**IMPORTANT**: Every time you obtain a credential (API key, token, secret), you MUST call \
`skill_save_config` — even if you also saved it to a local file as the SKILL.md instructed. \
The local file is for the skill's own use; `skill_save_config` is for the system to track and inject it.

##### 4. Installing Skills
**General rule**: Always install to `skills/<skill-name>/`. Never to `~/` or other paths.

**From ClawHub URL** (e.g. `https://clawhub.ai/author/skill-name`):
1. Extract slug (last path segment only, e.g. `skill-name`)
2. Run: `clawhub install <slug> --dir skills/ --force --no-input`
3. If rate-limited, wait 5s and retry
4. Verify: `skills/<slug>/SKILL.md` exists

**From GitHub or other URL**: Clone/download to `skills/<skill-name>/`

##### 5. When User Asks You to Learn a Skill (in conversation)
If a user sends a skill URL and asks you to learn/study it:
1. Install the skill (see §4 above)
2. Read SKILL.md to understand it
3. If registration is needed, complete it
4. Save credentials via `skill_save_config`
5. If it has periodic tasks (HEARTBEAT.md), create scheduled jobs via `job_create`
6. Call `skill_save_study_summary` with a Markdown summary
7. Report to the user what you did

##### 6. Human Assistance
Some skills require human intervention to activate (e.g., Twitter/X verification, \
email confirmation, OAuth browser login). When the SKILL.md describes a step that \
**only a human can complete**, you MUST use `send_message_to_user_directly` to:
1. Clearly explain what the human needs to do (with exact URLs, steps, codes)
2. Provide any claim tokens, verification codes, or links the human will need
3. Wait for the human to confirm completion before proceeding
Do NOT silently skip human-required steps — the skill will not function without them.
"""


class SkillModule(XYZBaseModule):
    """
    Skill Module - Manages Skills under the user's workspace

    Responsibilities:
    1. Scan the skills/ directory to discover installed Skills
    2. Generate Instructions to tell Claude which Skills are available (with paths)
    3. Provide Skill management APIs (install/remove/disable/enable)
    """

    def __init__(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        database_client: Optional[DatabaseClient] = None,
        instance_id: Optional[str] = None,
        instance_ids: Optional[List[str]] = None
    ):
        super().__init__(agent_id, user_id, database_client, instance_id, instance_ids)

        # Base path
        from xyz_agent_context.settings import settings
        self.base_path = Path(settings.base_working_path)

        # Skills directory (same as Claude Agent's cwd)
        self.skills_dir = self.base_path / f"{agent_id}_{user_id}" / "skills" if user_id else None

        # MCP Server port
        self.port = 7806

        # Instructions template
        # Note: Agent's cwd is already {base_working_path}/{agent_id}_{user_id}/
        # so paths in the prompt must use skills/ relative to cwd, not absolute paths
        self.instructions = SKILL_INSTRUCTIONS_TEMPLATE

    def get_config(self) -> ModuleConfig:
        """Return SkillModule configuration"""
        return ModuleConfig(
            name="SkillModule",
            priority=90,
            enabled=True,
            description="Manages user Skills, provides skill extension capabilities",
            module_type="capability",
        )

    # =========================================================================
    # Hooks
    # =========================================================================

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        """Scan skills directory and add Skills information to ctx_data"""
        logger.debug(f"SkillModule.hook_data_gathering() started for agent_id={self.agent_id}")

        skills = self._scan_skills()

        # Build Skills table with status column
        # Use skills/xxx format relative to Agent cwd to avoid duplication from absolute paths
        if skills:
            table = "| Skill | Description | Path | Status |\n"
            table += "|-------|-------------|------|--------|\n"
            for skill in skills:
                relative_path = f"skills/{skill.name}"
                # Determine config status
                if skill.requires_env and skill.env_configured is False:
                    missing = ", ".join(skill.requires_env)
                    status = f"⚠ needs: {missing}"
                else:
                    status = "✓ ready"
                table += f"| {skill.name} | {skill.description} | `{relative_path}/SKILL.md` | {status} |\n"
        else:
            table = "*No skills installed.*"

        # Store in ctx_data for use by get_instructions
        ctx_data.extra_data = ctx_data.extra_data or {}
        ctx_data.extra_data["skills_table"] = table
        ctx_data.extra_data["skills_count"] = len(skills)
        ctx_data.extra_data["available_skills"] = [s.model_dump() for s in skills]

        # Collect all configured env vars from enabled skills for runtime injection
        skill_env_vars = self.get_all_skill_env_vars()
        if skill_env_vars:
            ctx_data.extra_data["skill_env_vars"] = skill_env_vars
            logger.debug(f"Collected {len(skill_env_vars)} skill env vars for injection")

        logger.debug(f"SkillModule.hook_data_gathering() completed, found {len(skills)} skills")
        return ctx_data

    async def get_instructions(self, ctx_data: ContextData) -> str:
        """Return Skills-related Instructions"""
        skills_table = ""
        skills_count = 0

        if ctx_data.extra_data:
            skills_table = ctx_data.extra_data.get("skills_table", "")
            skills_count = ctx_data.extra_data.get("skills_count", 0)

        # Agent's cwd is already {base_working_path}/{agent_id}_{user_id}/
        # Use relative path skills/ in prompt to avoid path duplication
        if skills_count == 0:
            # Even with no skills, agent needs workspace rules and installation instructions
            return SKILL_INSTRUCTIONS_TEMPLATE.format(skills_table="*No skills installed.*")

        return self.instructions.format(skills_table=skills_table)

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        """
        Return MCP Server configuration

        SkillModule provides MCP tools for agent self-configuration:
        - skill_save_config: Save env var for a skill
        - skill_list_required_env: Query required env vars
        - skill_save_study_summary: Save structured study summary
        """
        return MCPServerConfig(
            server_name="skill_module",
            server_url=f"http://{mcp_host()}:{self.port}/sse",
            type="sse"
        )

    def create_mcp_server(self):
        """
        Create MCP Server, delegates to _skill_mcp_tools module.

        Tools are stateless — they accept agent_id + user_id as parameters
        and construct temporary SkillModule instances internally.
        """
        from xyz_agent_context.module.skill_module._skill_mcp_tools import create_skill_mcp_server
        return create_skill_mcp_server(self.port)

    # =========================================================================
    # Scanning Logic
    # =========================================================================

    def _scan_skills(self) -> List[SkillInfo]:
        """Scan skills directory, including directories without SKILL.md (e.g., skills auto-configured by agent)"""
        if not self.skills_dir or not self.skills_dir.exists():
            return []

        skills = []
        for skill_path in self.skills_dir.iterdir():
            if skill_path.is_dir() and not skill_path.name.startswith('.'):
                skill_md = skill_path / "SKILL.md"
                if skill_md.exists():
                    info = self._parse_skill_md(skill_md)
                    skills.append(info)
                else:
                    # Directory without SKILL.md (may be a skill auto-created by agent)
                    # Still list it, using directory name as the name
                    meta_file = skill_path / ".skill_meta.json"
                    meta_data = {}
                    if meta_file.exists():
                        try:
                            meta_data = json.loads(meta_file.read_text(encoding='utf-8'))
                        except Exception:
                            pass

                    info = SkillInfo(
                        name=skill_path.name,
                        description=meta_data.get('description', '(No SKILL.md found)'),
                        path=str(skill_path),
                        source_url=meta_data.get('source_url'),
                        installed_at=meta_data.get('installed_at'),
                        study_status=meta_data.get('study_status'),
                        study_result=meta_data.get('study_result'),
                        study_error=meta_data.get('study_error'),
                        studied_at=meta_data.get('studied_at'),
                    )
                    skills.append(info)

        return skills

    @staticmethod
    def _extract_env_vars_from_text(text: str) -> list[str]:
        """Extract environment variable names from markdown body text.

        Looks for patterns like:
        - Set GOG_ACCOUNT=...
        - export TAVILY_API_KEY=...
        - Needs OPENAI_API_KEY
        - `MY_VAR` in backticks
        """
        import re
        env_pattern = re.compile(r'\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b')
        candidates = set(env_pattern.findall(text))
        env_suffixes = ('_KEY', '_TOKEN', '_SECRET', '_ACCOUNT', '_URL',
                        '_PASSWORD', '_PASS', '_API', '_AUTH', '_CREDENTIAL')
        env_prefixes = ('API_', 'AWS_', 'GOOGLE_', 'OPENAI_', 'ANTHROPIC_',
                        'TAVILY_', 'GOG_', 'GITHUB_', 'SLACK_', 'DISCORD_')
        result = []
        for c in candidates:
            if any(c.endswith(s) for s in env_suffixes):
                result.append(c)
            elif any(c.startswith(s) for s in env_prefixes):
                result.append(c)
        return sorted(set(result))

    def _parse_skill_md(self, skill_md: Path) -> SkillInfo:
        """Parse SKILL.md frontmatter"""
        skill_dir = skill_md.parent
        source_url = None
        installed_at = None

        # Read .skill_meta.json (if exists)
        meta_file = skill_dir / ".skill_meta.json"
        meta_data = {}
        if meta_file.exists():
            try:
                meta_data = json.loads(meta_file.read_text(encoding='utf-8'))
            except Exception as e:
                logger.warning(f"Failed to read .skill_meta.json: {e}")

        # Extract fields from meta_data
        source_url = meta_data.get('source_url')
        installed_at = meta_data.get('installed_at')
        study_fields = {
            "study_status": meta_data.get('study_status'),
            "study_result": meta_data.get('study_result'),
            "study_error": meta_data.get('study_error'),
            "studied_at": meta_data.get('studied_at'),
        }

        # Extract requirements from .skill_meta.json (may have been set by study)
        meta_requires = meta_data.get('requires', {})
        meta_requires_env = meta_requires.get('env', []) if isinstance(meta_requires, dict) else []
        meta_requires_bins = meta_requires.get('bins', []) if isinstance(meta_requires, dict) else []

        # Read env_config to determine if all required vars are configured
        env_config = meta_data.get('env_config', {})

        try:
            content = skill_md.read_text(encoding='utf-8')
            if content.startswith('---'):
                parts = content.split('---', 2)
                if len(parts) >= 3:
                    fm = parts[1]
                    meta = yaml.safe_load(fm)
                    if meta:
                        # Parse structured metadata (e.g., clawdbot format)
                        fm_requires_env = []
                        fm_requires_bins = []
                        metadata_field = meta.get('metadata', {})
                        if isinstance(metadata_field, str):
                            try:
                                metadata_field = json.loads(metadata_field)
                            except (json.JSONDecodeError, TypeError):
                                metadata_field = {}
                        if isinstance(metadata_field, dict):
                            clawdbot = metadata_field.get('clawdbot', {})
                            if isinstance(clawdbot, dict):
                                requires = clawdbot.get('requires', {})
                                if isinstance(requires, dict):
                                    fm_requires_env = requires.get('env', [])
                                    fm_requires_bins = requires.get('bins', [])

                        # Scan markdown body for env var patterns
                        body_text = parts[2] if len(parts) >= 3 else ''
                        body_env = self._extract_env_vars_from_text(body_text)

                        # Merge frontmatter + body scan + meta.json requirements (union)
                        requires_env = sorted(set(fm_requires_env + body_env + meta_requires_env)) or None
                        requires_bins = sorted(set(fm_requires_bins + meta_requires_bins)) or None

                        # Check if all required env vars are configured
                        env_configured = None
                        if requires_env:
                            env_configured = all(env_config.get(v) for v in requires_env)

                        return SkillInfo(
                            name=meta.get('name', skill_dir.name),
                            description=meta.get('description', ''),
                            path=str(skill_dir),
                            version=meta.get('version'),
                            author=meta.get('author'),
                            source_url=source_url,
                            installed_at=installed_at,
                            requires_env=requires_env,
                            requires_bins=requires_bins,
                            env_configured=env_configured,
                            **study_fields,
                        )
        except Exception as e:
            logger.warning(f"Failed to parse SKILL.md at {skill_md}: {e}")

        # Fallback: use directory name as the name
        requires_env = sorted(set(meta_requires_env)) or None
        requires_bins = sorted(set(meta_requires_bins)) or None
        env_configured = None
        if requires_env:
            env_configured = all(env_config.get(v) for v in requires_env)

        return SkillInfo(
            name=skill_dir.name,
            description='',
            path=str(skill_dir),
            source_url=source_url,
            installed_at=installed_at,
            requires_env=requires_env,
            requires_bins=requires_bins,
            env_configured=env_configured,
            **study_fields,
        )

    def _save_skill_meta(
        self,
        skill_dir: Path,
        source_url: Optional[str] = None,
        source_type: str = "unknown"
    ) -> None:
        """Save Skill metadata to .skill_meta.json"""
        meta_data = {
            "source_url": source_url,
            "source_type": source_type,
            "installed_at": datetime.now().isoformat(),
        }
        meta_file = skill_dir / ".skill_meta.json"
        try:
            meta_file.write_text(json.dumps(meta_data, indent=2), encoding='utf-8')
            logger.debug(f"Saved skill metadata to {meta_file}")
        except Exception as e:
            logger.warning(f"Failed to save skill metadata: {e}")

    # =========================================================================
    # Study Status Management
    # =========================================================================

    def get_study_status(self, skill_name: str) -> dict:
        """Get the study status of a Skill"""
        if not self.skills_dir:
            return {"study_status": "idle"}

        skill_dir = self._resolve_skill_dir(skill_name)
        if not skill_dir:
            return {"study_status": "idle"}
        meta_file = skill_dir / ".skill_meta.json"
        if meta_file.exists():
            try:
                meta_data = json.loads(meta_file.read_text(encoding='utf-8'))
                return {
                    "study_status": meta_data.get("study_status", "idle"),
                    "study_result": meta_data.get("study_result"),
                    "study_error": meta_data.get("study_error"),
                    "studied_at": meta_data.get("studied_at"),
                }
            except Exception as e:
                logger.warning(f"Failed to read study status: {e}")

        return {"study_status": "idle"}

    def set_study_status(
        self,
        skill_name: str,
        status: str,
        result: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update Skill's study status to .skill_meta.json"""
        if not self.skills_dir:
            return

        skill_dir = self._resolve_skill_dir(skill_name)
        if not skill_dir:
            logger.warning(f"Cannot set study status: skill directory not found for '{skill_name}'")
            return
        meta_file = skill_dir / ".skill_meta.json"

        # Read existing metadata
        meta_data = {}
        if meta_file.exists():
            try:
                meta_data = json.loads(meta_file.read_text(encoding='utf-8'))
            except Exception:
                pass

        # Update study fields
        meta_data["study_status"] = status
        if result is not None:
            meta_data["study_result"] = result
        if error is not None:
            meta_data["study_error"] = error
        if status == "completed":
            meta_data["studied_at"] = datetime.now().isoformat()

        try:
            meta_file.write_text(
                json.dumps(meta_data, indent=2, ensure_ascii=False),
                encoding='utf-8'
            )
            logger.debug(f"Updated study status for '{skill_name}': {status}")
        except Exception as e:
            logger.warning(f"Failed to update study status: {e}")

    # =========================================================================
    # Environment Configuration Management
    # =========================================================================

    def _resolve_skill_dir(self, skill_name: str) -> Optional[Path]:
        """Resolve the actual directory for a skill by name.

        The skill name (from SKILL.md frontmatter) may differ from the directory name
        (e.g., frontmatter has 'tavily-search' but directory is 'openclaw-tavily-search').
        This method finds the correct directory by checking:
        1. Direct match: skills/{skill_name}/
        2. Scan all skills and match by parsed name
        """
        if not self.skills_dir:
            return None
        # Direct match
        direct = self.skills_dir / skill_name
        if direct.exists() and direct.is_dir():
            return direct
        # Scan and match by parsed name from SKILL.md
        for skill_path in self.skills_dir.iterdir():
            if skill_path.is_dir() and not skill_path.name.startswith('.'):
                skill_md = skill_path / "SKILL.md"
                if skill_md.exists():
                    try:
                        content = skill_md.read_text(encoding='utf-8')
                        if content.startswith('---'):
                            parts = content.split('---', 2)
                            if len(parts) >= 3:
                                meta = yaml.safe_load(parts[1])
                                if meta and meta.get('name') == skill_name:
                                    return skill_path
                    except Exception:
                        pass
        return None

    def _read_skill_meta(self, skill_name: str) -> dict:
        """Read .skill_meta.json for a skill, returns empty dict if not found"""
        skill_dir = self._resolve_skill_dir(skill_name)
        if not skill_dir:
            return {}
        meta_file = skill_dir / ".skill_meta.json"
        if meta_file.exists():
            try:
                return json.loads(meta_file.read_text(encoding='utf-8'))
            except Exception as e:
                logger.warning(f"Failed to read .skill_meta.json for '{skill_name}': {e}")
        return {}

    def _write_skill_meta(self, skill_name: str, meta_data: dict) -> None:
        """Write .skill_meta.json for a skill"""
        skill_dir = self._resolve_skill_dir(skill_name)
        if not skill_dir:
            logger.warning(f"Cannot write .skill_meta.json: skill dir not found for '{skill_name}'")
            return
        meta_file = skill_dir / ".skill_meta.json"
        try:
            meta_file.write_text(
                json.dumps(meta_data, indent=2, ensure_ascii=False),
                encoding='utf-8'
            )
        except Exception as e:
            logger.warning(f"Failed to write .skill_meta.json for '{skill_name}': {e}")

    def get_skill_requirements(self, skill_name: str) -> dict:
        """Get the requirements dict from .skill_meta.json"""
        meta_data = self._read_skill_meta(skill_name)
        return meta_data.get('requires', {})

    def get_skill_env_config(self, skill_name: str) -> dict:
        """Get env_config from .skill_meta.json (var_name -> base64-encoded value)"""
        meta_data = self._read_skill_meta(skill_name)
        return meta_data.get('env_config', {})

    def set_skill_env_config(self, skill_name: str, env_config: dict) -> None:
        """Save env var values to .skill_meta.json (base64 encoded)"""
        import base64
        meta_data = self._read_skill_meta(skill_name)

        encoded_config = {}
        for key, value in env_config.items():
            if value:  # Only store non-empty values
                encoded_config[key] = base64.b64encode(value.encode('utf-8')).decode('utf-8')

        # Merge with existing (allow partial updates)
        existing = meta_data.get('env_config', {})
        existing.update(encoded_config)
        meta_data['env_config'] = existing

        self._write_skill_meta(skill_name, meta_data)
        logger.info(f"Saved env config for skill '{skill_name}': {list(env_config.keys())}")

    def update_requirements(self, skill_name: str, env_list: list, bins_list: list) -> None:
        """Merge new requirements into .skill_meta.json (union with existing)"""
        meta_data = self._read_skill_meta(skill_name)
        existing = meta_data.get('requires', {})
        existing_env = existing.get('env', []) if isinstance(existing, dict) else []
        existing_bins = existing.get('bins', []) if isinstance(existing, dict) else []

        merged_env = sorted(set(existing_env + env_list))
        merged_bins = sorted(set(existing_bins + bins_list))

        meta_data['requires'] = {
            'env': merged_env,
            'bins': merged_bins,
        }
        self._write_skill_meta(skill_name, meta_data)
        logger.info(f"Updated requirements for skill '{skill_name}': env={merged_env}, bins={merged_bins}")

    def get_all_skill_env_vars(self) -> dict:
        """
        Collect all configured env vars from all enabled skills.
        Returns a merged dict of plaintext env var name -> value.
        """
        import base64
        all_env = {}
        skills = self._scan_skills()
        for skill in skills:
            meta_data = self._read_skill_meta(skill.name)
            env_config = meta_data.get('env_config', {})
            for key, encoded_value in env_config.items():
                try:
                    value = base64.b64decode(encoded_value).decode('utf-8')
                    if key in all_env and all_env[key] != value:
                        logger.warning(
                            f"Env var '{key}' conflict: skill '{skill.name}' overrides previous value"
                        )
                    all_env[key] = value
                except Exception:
                    logger.warning(f"Failed to decode env var '{key}' for skill '{skill.name}'")
        return all_env

    # =========================================================================
    # Skill Management Methods (called by API layer)
    # =========================================================================

    def list_skills(self, include_disabled: bool = False) -> List[SkillInfo]:
        """List all Skills"""
        skills = self._scan_skills()

        if include_disabled and self.skills_dir:
            disabled_dir = self.skills_dir / ".disabled"
            if disabled_dir.exists():
                for skill_path in disabled_dir.iterdir():
                    if skill_path.is_dir():
                        skill_md = skill_path / "SKILL.md"
                        if skill_md.exists():
                            info = self._parse_skill_md(skill_md)
                            info.disabled = True
                            skills.append(info)

        return skills

    def install_skill(self, zip_file_path: Path) -> SkillInfo:
        """Install Skill from zip file"""
        if not self.skills_dir:
            raise ValueError("skills_dir is not configured (user_id is required)")

        self.skills_dir.mkdir(parents=True, exist_ok=True)

        # Extract to temp directory for validation first
        temp_dir = Path(tempfile.mkdtemp())
        try:
            self._extract_zip_safely(zip_file_path, temp_dir)

            # Find the directory containing SKILL.md
            skill_root = self._find_skill_root(temp_dir)
            if not skill_root:
                raise ValueError("Invalid skill: SKILL.md not found in zip")

            # Parse skill info
            skill_md = skill_root / "SKILL.md"
            info = self._parse_skill_md(skill_md)

            # Move to target directory
            safe_skill_name = sanitize_filename(info.name, label="skill name")
            target_dir = ensure_within_directory(self.skills_dir, safe_skill_name, label="skill name")
            if target_dir.exists():
                shutil.rmtree(target_dir)

            shutil.move(str(skill_root), str(target_dir))

            # Save installation metadata
            self._save_skill_meta(target_dir, source_url=None, source_type="zip")

            # Update path and metadata
            info.name = safe_skill_name
            info.path = str(target_dir)
            info.installed_at = datetime.now().isoformat()
            logger.info(f"Installed skill '{info.name}' to {target_dir}")
            return info

        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    def _find_skill_root(self, extract_dir: Path) -> Optional[Path]:
        """Find the directory containing SKILL.md in the extracted directory"""
        if (extract_dir / "SKILL.md").exists():
            return extract_dir

        for subdir in extract_dir.iterdir():
            if subdir.is_dir() and (subdir / "SKILL.md").exists():
                return subdir

        return None

    def _extract_zip_safely(self, zip_file_path: Path, target_dir: Path) -> None:
        """Extract a skill archive while rejecting zip-slip style paths."""
        max_entries = 500
        max_uncompressed_bytes = 100 * 1024 * 1024

        with zipfile.ZipFile(zip_file_path, "r") as zip_ref:
            members = zip_ref.infolist()
            if len(members) > max_entries:
                raise ValueError("Invalid skill package: too many files")

            total_uncompressed = 0
            target_root = target_dir.resolve(strict=False)
            for member in members:
                member_path = validate_zip_member_path(member.filename)
                total_uncompressed += member.file_size
                if total_uncompressed > max_uncompressed_bytes:
                    raise ValueError("Invalid skill package: uncompressed size is too large")

                destination = (target_dir / member_path).resolve(strict=False)
                if target_root not in destination.parents and destination != target_root:
                    raise ValueError("Invalid skill package: path traversal not allowed")

                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue

                destination.parent.mkdir(parents=True, exist_ok=True)
                with zip_ref.open(member) as src, open(destination, "wb") as dst:
                    shutil.copyfileobj(src, dst)

    def install_from_github(self, url: str, branch: str = "main") -> SkillInfo:
        """
        Install Skill from GitHub

        Args:
            url: GitHub repository URL, supported formats:
                 - https://github.com/user/repo
                 - github:user/repo (shorthand)
            branch: Branch name, defaults to main

        Returns:
            Successfully installed SkillInfo
        """
        if not self.skills_dir:
            raise ValueError("skills_dir is not configured (user_id is required)")

        # Parse URL (supports shorthand format)
        if url.startswith("github:"):
            url = f"https://github.com/{url[7:]}"

        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {"github.com", "www.github.com"}:
            raise ValueError("Only https://github.com repositories are supported")
        if parsed.username or parsed.password:
            raise ValueError("GitHub URLs with embedded credentials are not allowed")
        if not parsed.path or parsed.path == "/":
            raise ValueError("Invalid GitHub repository URL")

        # Clone to temp directory
        temp_dir = Path(tempfile.mkdtemp())
        try:
            logger.info(f"Cloning {url} (branch: {branch}) to {temp_dir}")
            subprocess.run(
                ["git", "clone", "--depth", "1", "-b", branch, url, str(temp_dir)],
                check=True,
                capture_output=True,
                text=True
            )

            # Verify SKILL.md exists
            skill_md = temp_dir / "SKILL.md"
            if not skill_md.exists():
                raise ValueError(f"Invalid skill: SKILL.md not found in {url}")

            # Parse skill info
            info = self._parse_skill_md(skill_md)

            # Move to target directory
            self.skills_dir.mkdir(parents=True, exist_ok=True)
            safe_skill_name = sanitize_filename(info.name, label="skill name")
            target_dir = ensure_within_directory(self.skills_dir, safe_skill_name, label="skill name")

            if target_dir.exists():
                shutil.rmtree(target_dir)

            # Remove .git directory (version control not needed)
            git_dir = temp_dir / ".git"
            if git_dir.exists():
                shutil.rmtree(git_dir)

            shutil.move(str(temp_dir), str(target_dir))

            # Save installation metadata (including source URL)
            self._save_skill_meta(target_dir, source_url=url, source_type="github")

            # Update path info and metadata
            info.name = safe_skill_name
            info.path = str(target_dir)
            info.source_url = url
            info.installed_at = datetime.now().isoformat()
            logger.info(f"Installed skill '{info.name}' from GitHub to {target_dir}")
            return info

        except subprocess.CalledProcessError as e:
            raise ValueError(f"Failed to clone {url}: {e.stderr}")
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    def remove_skill(self, skill_name: str) -> bool:
        """Remove a Skill"""
        if not self.skills_dir:
            return False

        skill_path = self.skills_dir / skill_name
        if skill_path.exists():
            shutil.rmtree(skill_path)
            logger.info(f"Removed skill '{skill_name}' from {skill_path}")
            return True

        # Also check disabled directory
        disabled_path = self.skills_dir / ".disabled" / skill_name
        if disabled_path.exists():
            shutil.rmtree(disabled_path)
            logger.info(f"Removed disabled skill '{skill_name}' from {disabled_path}")
            return True

        return False

    def disable_skill(self, skill_name: str) -> bool:
        """Disable a Skill"""
        if not self.skills_dir:
            return False

        skill_path = self.skills_dir / skill_name
        disabled_dir = self.skills_dir / ".disabled"

        if skill_path.exists():
            disabled_dir.mkdir(exist_ok=True)
            shutil.move(str(skill_path), str(disabled_dir / skill_name))
            logger.info(f"Disabled skill '{skill_name}'")
            return True

        return False

    def enable_skill(self, skill_name: str) -> bool:
        """Enable a Skill"""
        if not self.skills_dir:
            return False

        disabled_path = self.skills_dir / ".disabled" / skill_name

        if disabled_path.exists():
            shutil.move(str(disabled_path), str(self.skills_dir / skill_name))
            logger.info(f"Enabled skill '{skill_name}'")
            return True

        return False

    def get_skill(self, skill_name: str) -> Optional[SkillInfo]:
        """Get a Skill by name"""
        all_skills = self.list_skills(include_disabled=True)
        for skill in all_skills:
            if skill.name == skill_name:
                return skill
        return None
