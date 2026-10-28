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
- Located at {base}/{agent_workspace_relpath}/skills/ (resolved via
  utils.workspace_paths; nested {user_id}/{agent_id} under current layout)
- Same as Claude Agent's cwd

MCP Tools:
- skill_save_config: Save env var for a skill (credentials auto-injected at runtime)
- skill_list_required_env: Query required env vars and config status
- skill_save_study_summary: Save structured Markdown study summary
"""

import os
import re
import shutil
import tempfile
import subprocess
import zipfile
import json
from pathlib import Path
from typing import Optional, List, Set, Tuple
from datetime import datetime
from urllib.parse import urlparse

import yaml
from loguru import logger

from narranexus.platform.utils.workspace_paths import safe_id_segment

from narranexus.platform.module_system.base import XYZBaseModule, mcp_server_url
from narranexus.platform.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
)
from narranexus.contracts.openclaw import OPENCLAW_ALIASES
from narranexus.platform.schema.skill_schema import SkillInfo
from narranexus.platform.utils.plugin_contributions import plugin_skills
from narranexus.platform.utils import DatabaseClient
from narranexus.platform.utils import file_safety as _file_safety
from narranexus.platform.utils.file_safety import (
    ensure_within_directory,
    sanitize_filename,
    validate_zip_member_path,
)


# =============================================================================
# Shared prompt constants
#
# Two workspace-rule variants — one for CLOUD (multi-tenant shared host,
# strict isolation) and one for LOCAL (user's own machine, relaxed access
# with advisory transparency). The right one is rendered at prompt-build
# time based on the deployment mode reported by BasicInfoModule.
# =============================================================================

WORKSPACE_RULES_CLOUD = (
    "- All files you create or download MUST stay inside your workspace "
    "(`skills/<skill-name>/` or current working directory). Paths like "
    "`~/`, `/etc/`, `/tmp/` outside the workspace, `~/.config/`, `~/.aws/`, "
    "`~/.gnupg/` are **blocked by the sandbox**.\n"
    "- When a SKILL.md suggests paths like `~/.foo/` or `~/.config/foo/`, "
    "**remap them** to `skills/<skill-name>/` instead.\n"
    "- **Global installation is blocked in cloud.** `brew install`, "
    "`npm install -g`, `yarn global add`, `apt-get install`, `sudo ...`, "
    "and `pip install` without `--target=` / `--user` all fail. Drop "
    "dependencies into `skills/<skill-name>/` instead: `pip install "
    "--target=./skills/<name>/libs <pkg>` or `npm install <pkg>` (no `-g`) "
    "inside the skill directory.\n"
    "- **If a SKILL.md demands a global CLI / system package you can't "
    "install** (e.g. needs `brew install ...`), do NOT keep trying. Call "
    "the owner-facing tool on your desk this turn and tell the user: "
    '*"This skill '
    "needs a global CLI install, which this cloud deployment does not "
    "yet support. Please either pick a different skill, or run this on "
    'a local NarraNexus install."*\n'
    "- **Pre-installed CLIs** already in PATH: `claude`, `lark-cli`, "
    "`arena` / `npx arena`. Use them directly.\n"
    "- **Credentials and API keys** — do BOTH of the following:\n"
    "  1. If the SKILL.md instructs you to save to a local file (e.g., "
    "`credentials.json`), do so inside `skills/<skill-name>/` (remapped "
    "path). **Never** write credentials to `~/.config/`, `~/.aws/`, "
    "`/etc/`, or other global locations — it would leak to other users.\n"
    "  2. **Also** call `skill_save_config` for each key — this registers "
    "it in the system so it appears in the frontend config panel and is "
    "auto-injected at runtime."
)

WORKSPACE_RULES_LOCAL = (
    "- `skills/<skill-name>/` is the **preferred** home for files you "
    "create as part of a skill (keeps related things together), but this "
    "is the user's own machine and you MAY read/write outside the "
    "workspace when the task calls for it (e.g. `~/Documents/`, "
    "`/tmp/`, a project directory the user points you at).\n"
    "- **Global installation is allowed.** You MAY run `brew install`, "
    "`npm install -g`, `pip install`, etc. when a skill needs it. "
    "**Good practice (not strict)**: before a large global change "
    "(new binary, modifying system PATH), briefly mention to the user "
    "tell your owner what you're about to install "
    "and where, so they know what changed on their computer.\n"
    "- **Credentials and API keys** — do BOTH of the following:\n"
    "  1. If the SKILL.md specifies where credentials live (e.g., "
    "`~/.config/foo/` or `credentials.json` inside the skill dir), save "
    "them there. If the skill is happy with a workspace-local file, "
    "prefer `skills/<skill-name>/`. **Good practice**: tell the user "
    "tell your owner where the credential was "
    "saved so they can rotate / revoke it later.\n"
    "  2. **Also** call `skill_save_config` for each key — this registers "
    "it in the system so it appears in the frontend config panel and is "
    "auto-injected at runtime."
)


# Appended to BOTH deployment variants (rule #7: the two run modes must
# behave identically). Keeps skill state changes on the managed path so the
# audit DB and the filesystem cannot drift by agent action.
SKILL_MANAGEMENT_RULES = (
    "- **Never create, delete, or modify skill directories under `skills/` "
    "by hand** (no `mkdir`/`rm`/`cp`/`mv` on skill folders). To install, "
    "uninstall, or update a skill, always use the dedicated tools: "
    "`skill_search_marketplace`, `skill_install`, `skill_uninstall`. "
    "Hand-edited skill state is flagged as unmanaged and may be ignored."
)

WORKSPACE_RULES_CLOUD = WORKSPACE_RULES_CLOUD + "\n" + SKILL_MANAGEMENT_RULES
WORKSPACE_RULES_LOCAL = WORKSPACE_RULES_LOCAL + "\n" + SKILL_MANAGEMENT_RULES


def _resolve_workspace_rules(ctx_data: "ContextData") -> str:
    """Pick the cloud or local workspace-rules block for the current run.

    Falls back to cloud (the stricter set) when ``deployment_mode`` is
    missing so we never accidentally hand a local-style prompt to a
    cloud agent.
    """
    mode = getattr(ctx_data, "deployment_mode", None)
    if mode == "local":
        return WORKSPACE_RULES_LOCAL
    return WORKSPACE_RULES_CLOUD


SKILL_INSTRUCTIONS_TEMPLATE = """\
#### Available Skills

Your skills directory: `skills/` (relative to your current working directory)

{skills_table}

##### 1. Using Skills
- When a task matches a Skill, read its `SKILL.md` using `cat`
- Follow the instructions; access referenced files (guides, scripts) as needed
- For scripts, execute them and use the output (don't read the source code)

##### 2. Workspace & File Storage Rules
{workspace_rules}

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
**only a human can complete**, you MUST use the owner-facing tool on your desk to:
1. Clearly explain what the human needs to do (with exact URLs, steps, codes)
2. Provide any claim tokens, verification codes, or links the human will need
3. Wait for the human to confirm completion before proceeding
Do NOT silently skip human-required steps — the skill will not function without them.
"""


# Repo-vendored built-in skills shipped with the app. Each subdirectory is a
# standard skill (SKILL.md + assets) that gets materialized into every agent
# workspace on run. Requires no credentials; the referenced CLIs are
# pre-provisioned onto PATH by the shell/build layer (see run.sh /
# Dockerfile.manyfold / build-desktop.sh), never installed by the agent.
BUILTIN_SKILLS_DIR = Path(__file__).parent / "builtin_skills"

# Env vars the PLATFORM can satisfy from the user's provider configuration
# at run time (see _resolve_platform_env). Declared-but-unconfigured vars in
# this set do NOT flag a skill as "Needs Config" — they are auto-injected.
PLATFORM_RESOLVED_ENV = ("NETMIND_API_KEY",)


def configured_env_var_names(env_config: dict) -> Set[str]:
    """The single source of truth for "which stored env vars are usable config".

    A var counts only if its stored value is present AND decryptable. A value
    that is ciphertext this key can no longer open (the SecretBox key rotated
    or was lost) does NOT count — otherwise every "is this configured?" check
    (list/detail routes, the skill_list_required_env MCP tool, the agent's
    skills table, install/upgrade config_required) shows the skill as ready
    while it fails at run time on a missing/garbage credential (2026-08-01).
    This rule used to live inline as ``bool(env_config.get(v))`` in six places
    with drifting semantics; keep it here so a fix lands once.

    Takes a raw env_config DICT (not a skill name), so it is agnostic to
    whether the skill is enabled or disabled — the caller reads the meta from
    the skill's own directory. Platform-resolved vars are a SEPARATE concern
    handled by each caller (they may be satisfied without a stored value).

    TOTAL by contract: this runs inside ``_parse_skill_md`` (once a pure
    filesystem path) and behind ``GET /api/skills`` and the agent's per-turn
    hook. A malformed meta (agent-writable file) or a key-load failure must
    NOT crash the panel or drop the whole agent's credential injection — every
    unreadable value degrades to "not configured" (fail-CLOSED), never to a
    returned ciphertext (fail-open). Decryption itself is delegated to the
    (total) ``decrypt_env_config`` so this and the injection path share ONE
    decryptor; the ``plain`` it returns already excludes undecryptable, corrupt,
    and blank-stored values. We ALSO drop values that decrypt to an empty string
    (e.g. ``encrypt("")`` — a real token a scrubbed bundle restore can leave
    behind): "decryptable" is not "usable", and an empty credential reads as NOT
    configured so the UI prompts a re-enter instead of showing a green card that
    injects an empty key at run time.
    """
    if not isinstance(env_config, dict):
        # Fast return: skip get_secret_box() entirely for a malformed meta.
        # decrypt_env_config is itself total on non-dict; this is an entry guard,
        # not a second sanitization layer.
        return set()
    from narranexus.platform.marketplace import get_secret_box

    try:
        box = get_secret_box()
    except Exception as e:  # noqa: BLE001 — bad SKILL_SECRETS_KEY / unwritable dir
        # A process-level fact (misconfigured key), not per-skill; log at debug
        # so it doesn't repeat per skill × per scan. The one loud, contextful
        # ERROR is emitted by the injection path (get_all_skill_env_vars).
        logger.debug(f"SecretBox unavailable; treating all creds as unconfigured: {e}")
        return set()

    plain, _, _ = box.decrypt_env_config(env_config)
    return {name for name, value in plain.items() if value}


def env_config_status(
    requires_env: Optional[List[str]], env_config: dict
) -> Tuple[Optional[bool], Optional[List[str]]]:
    """Parse-time config status for a skill: (env_configured, platform_assumed).

    Single source for the optimistic, filesystem-only status computed in both
    ``_parse_skill_md`` return paths. A required var is satisfied when it is
    self-stored + decryptable (``configured_env_var_names``) OR it is
    platform-resolvable (``PLATFORM_RESOLVED_ENV``, auto-injected at run time).

    ``platform_assumed`` lists exactly the vars that count as configured ONLY
    because of the platform assumption AND are NOT self-stored — the API layer
    later validates just these against the user's provider config and downgrades
    the ones the platform can't actually satisfy, WITHOUT re-reading meta. A
    self-stored platform var is left OUT of this list so it is never wrongly
    downgraded (the 🟡1 reverse false-negative: a user who manually entered
    NETMIND_API_KEY in the Skill tab must stay "configured").
    """
    if not requires_env:
        return None, None
    configured = configured_env_var_names(env_config)
    env_configured = all(
        v in configured or v in PLATFORM_RESOLVED_ENV for v in requires_env
    )
    platform_assumed = [
        v for v in requires_env
        if v in PLATFORM_RESOLVED_ENV and v not in configured
    ] or None
    return env_configured, platform_assumed


async def platform_env_available(db, user_id: Optional[str]) -> set:
    """Which PLATFORM_RESOLVED_ENV vars are actually satisfiable for this user.

    Used by the API layer to show TRUTHFUL config status: a skill declaring
    NETMIND_API_KEY shows "configured" only when the user really has a
    NetMind provider row (or an explicit skill-level value)."""
    available: set = set()
    if db is None or not user_id:
        return available
    for var in PLATFORM_RESOLVED_ENV:
        if var == "NETMIND_API_KEY":
            try:
                row = await db.get_one(
                    "user_providers",
                    {"user_id": user_id, "source": "netmind", "protocol": "openai"},
                )
                if row and row.get("api_key"):
                    available.add(var)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"platform_env_available({var}) lookup failed: {e}")
    return available


# The OpenClaw / ClawHub skill format declares runtime requirements under
# ``metadata.<name>`` where <name> is any of the project's names; the vocabulary
# is ``narranexus.contracts.openclaw.OPENCLAW_ALIASES`` (shared with the migration
# scanner, so both sides recognise the same installs). First present key wins,
# current name first, so a skill published under any of the names gates its
# env/bins the same way.
SKILL_METADATA_KEYS: tuple[str, ...] = OPENCLAW_ALIASES


def _skill_runtime_requires(metadata_field: dict) -> dict:
    """``requires`` block from the first recognised skill-ecosystem key, else empty."""
    for key in SKILL_METADATA_KEYS:
        block = metadata_field.get(key)
        if isinstance(block, dict):
            requires = block.get("requires", {})
            return requires if isinstance(requires, dict) else {}
    return {}


# A branch name is user input placed on the git command line. Allowed alphabet
# only; a leading "-" (option injection), ".." (ref traversal) and anything
# else (whitespace, shell metacharacters) are refused before git runs.
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def _validate_git_branch(branch: str) -> None:
    """Raise ValueError unless ``branch`` is a safe git branch name."""
    if (
        not branch
        or branch.startswith("-")
        or ".." in branch
        or not _BRANCH_RE.fullmatch(branch)
    ):
        raise ValueError(
            "Invalid branch name: use letters, digits, '.', '_', '-' or '/' "
            "(must not start with '-' or contain '..')"
        )


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
        instance_ids: Optional[List[str]] = None,
    ):
        super().__init__(agent_id, user_id, database_client, instance_id, instance_ids)

        # Base path
        from narranexus.platform.settings import settings

        self.base_path = Path(settings.base_working_path)

        # Skills directory (same as Claude Agent's cwd)
        from narranexus.platform.utils.workspace_paths import agent_workspace_relpath

        self.skills_dir = self.base_path / agent_workspace_relpath(agent_id, user_id) / "skills" if user_id else None

        # MCP Server port

        # Instructions template
        # Note: Agent's cwd is already {base_working_path}/{agent_id}_{user_id}/
        # so paths in the prompt must use skills/ relative to cwd, not absolute paths
        self.instructions = SKILL_INSTRUCTIONS_TEMPLATE

    @staticmethod
    def get_config() -> ModuleConfig:
        """Return SkillModule configuration"""
        return ModuleConfig(
            name="SkillModule",
            long_running_instances=True,
            always_load=True,
            instance_prefix="skill",
            priority=90,
            enabled=True,
            description="Manages user Skills, provides skill extension capabilities",
            module_type="capability",
        )

    # =========================================================================
    # Hooks
    # =========================================================================

    async def gather(self, ctx_data: ContextData) -> ContextData:
        """Scan skills directory and add Skills information to ctx_data"""
        logger.debug(f"SkillModule.gather() started for agent_id={self.agent_id}")

        # Materialize repo-vendored built-in skills before scanning so they
        # appear in the table on the very first run.
        self._materialize_builtin_skills()

        skills = self._scan_skills()

        # Build Skills table with status column
        # Use skills/xxx format relative to Agent cwd to avoid duplication from absolute paths
        if skills:
            table = "| Skill | Description | Path | Status |\n"
            table += "|-------|-------------|------|--------|\n"
            for skill in skills:
                # Plugin skills live outside the workspace: point at their real path.
                relative_path = skill.path if skill.source_type == "plugin" else f"skills/{skill.name}"
                # Determine config status
                if skill.requires_env and skill.env_configured is False:
                    missing = ", ".join(skill.requires_env)
                    status = f"⚠ needs: {missing}"
                else:
                    status = "✓ ready"
                table += f"| {skill.name} | {skill.description} | `{relative_path}/SKILL.md` | {status} |\n"
        else:
            table = "*No skills installed.*"

        # Store in ctx_data for use by contribute_instructions
        ctx_data.extra_data = ctx_data.extra_data or {}
        ctx_data.extra_data["skills_table"] = table
        ctx_data.extra_data["skills_count"] = len(skills)
        ctx_data.extra_data["available_skills"] = [s.model_dump() for s in skills]

        # Collect all configured env vars from enabled skills for runtime injection
        skill_env_vars = self.get_all_skill_env_vars()
        skill_env_vars = await self._resolve_platform_env(skill_env_vars, skills)
        if skill_env_vars:
            ctx_data.extra_data["skill_env_vars"] = skill_env_vars
            logger.debug(f"Collected {len(skill_env_vars)} skill env vars for injection")

        logger.debug(f"SkillModule.gather() completed, found {len(skills)} skills")
        return ctx_data

    async def contribute_instructions(self, ctx_data: ContextData) -> str:
        """Return Skills-related Instructions"""
        skills_table = ""
        skills_count = 0

        if ctx_data.extra_data:
            skills_table = ctx_data.extra_data.get("skills_table", "")
            skills_count = ctx_data.extra_data.get("skills_count", 0)

        # Deployment mode (populated by BasicInfoModule.gather)
        # decides whether the agent sees the strict cloud rules or the
        # relaxed local rules.
        workspace_rules = _resolve_workspace_rules(ctx_data)

        # Agent's cwd is already {base_working_path}/{agent_id}_{user_id}/
        # Use relative path skills/ in prompt to avoid path duplication
        if skills_count == 0:
            # Even with no skills, agent needs workspace rules and installation instructions
            return SKILL_INSTRUCTIONS_TEMPLATE.format(
                skills_table="*No skills installed.*",
                workspace_rules=workspace_rules,
            )

        return self.instructions.format(
            skills_table=skills_table,
            workspace_rules=workspace_rules,
        )

    async def mcp_server(self) -> Optional[MCPServerConfig]:
        """
        Return MCP Server configuration

        SkillModule provides MCP tools for agent self-configuration:
        - skill_save_config: Save env var for a skill
        - skill_list_required_env: Query required env vars
        - skill_save_study_summary: Save structured study summary
        """
        return MCPServerConfig(
            server_name="skill_module", server_url=mcp_server_url("skill_module"), type="sse"
        )

    def create_mcp_server(self):
        """
        Create MCP Server, delegates to _skill_mcp_tools module.

        Tools are stateless — they accept agent_id + user_id as parameters
        and construct temporary SkillModule instances internally.
        """
        from narranexus_plugins.skill_module._skill_mcp_tools import create_skill_mcp_server

        return create_skill_mcp_server()

    # =========================================================================
    # Built-in Skills
    # =========================================================================

    def _materialize_builtin_skills(self) -> None:
        """Materialize repo-vendored built-in skills into the workspace.

        Built-in skills ship with the app under ``BUILTIN_SKILLS_DIR``. On every
        run we copy any that are not yet present in the agent workspace into
        ``skills/<name>/`` and tag them ``builtin: true`` in ``.skill_meta.json``.
        This keeps their ``SKILL.md`` inside the workspace so ``cat
        skills/<name>/SKILL.md`` stays within the cloud read-guard boundary.

        Idempotent and disable-aware: a built-in is skipped when either
        ``skills/<name>/`` or ``skills/.disabled/<name>/`` already exists, so a
        user who disabled it is not overridden by a resurrection on the next run.
        """
        if not self.skills_dir or not BUILTIN_SKILLS_DIR.exists():
            return

        disabled_dir = self.skills_dir / ".disabled"
        # sorted(): every directory walk in this module is name-ordered (R4d).
        # See _scan_skills() for why — readdir order is filesystem-defined and
        # reaches the system prompt.
        for src in sorted(BUILTIN_SKILLS_DIR.iterdir(), key=lambda p: p.name):
            if not src.is_dir() or src.name.startswith("."):
                continue
            name = src.name
            dest = self.skills_dir / name
            if dest.exists() or (disabled_dir / name).exists():
                # Already materialized, or intentionally disabled by the user.
                continue
            try:
                self.skills_dir.mkdir(parents=True, exist_ok=True)
                # Stage into a unique temp dir, then atomically rename into
                # place. Two concurrent runs (threads sharing this workspace, or
                # separate processes) could both pass the exists() check above;
                # a direct copytree to `dest` would then race and the loser would
                # raise FileExistsError — swallowed as a warning, masking the
                # race. mkdtemp gives each racer a private staging dir, and
                # os.rename onto an already-placed `dest` fails cleanly so the
                # loser simply discards its copy. Result is materialize-once with
                # no spurious warning.
                staging = Path(tempfile.mkdtemp(prefix=f".materialize.{name}.", dir=self.skills_dir))
                shutil.copytree(src, staging, dirs_exist_ok=True)
                self._write_builtin_meta(staging)
                try:
                    os.rename(staging, dest)
                except OSError:
                    # Another run won the race and placed `dest` first.
                    shutil.rmtree(staging, ignore_errors=True)
                    continue
                logger.info(f"Materialized built-in skill '{name}' into {dest}")
            except Exception as e:
                logger.warning(f"Failed to materialize built-in skill '{name}': {e}")

    @staticmethod
    def _write_builtin_meta(skill_dir: Path) -> None:
        """Write .skill_meta.json for a freshly materialized built-in skill."""
        meta_data = {
            "source_type": "builtin",
            "builtin": True,
            "installed_at": datetime.now().isoformat(),
        }
        meta_file = skill_dir / ".skill_meta.json"
        try:
            meta_file.write_text(json.dumps(meta_data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to write built-in skill metadata: {e}")

    # =========================================================================
    # Scanning Logic
    # =========================================================================

    def _scan_skills(self) -> List[SkillInfo]:
        """Scan skills directory, including directories without SKILL.md (e.g., skills auto-configured by agent)

        Iteration is name-sorted, NOT raw readdir order (R4d, 2026-07-28).
        This list becomes the skills table in the system prompt
        (gather -> ctx_data.extra_data["skills_table"] ->
        contribute_instructions), and ``Path.iterdir()`` yields whatever order the
        filesystem hands back — APFS returns creation-ish order, not
        alphabetical (verified: a live workspace listed as officecli,
        home-assistant-setup, netmind-transcribe, netmind-vision). Because
        _materialize_builtin_skills() runs every round and the agent can
        create/remove skill directories mid-conversation, any such change
        reshuffles the whole table at IDENTICAL total length — a same-length
        reorder that no byte-count diagnostic can see and that punctures the
        cacheable system-prompt prefix at the first transposed row.
        """
        skills: List[SkillInfo] = []
        if self.skills_dir and self.skills_dir.exists():
            skills.extend(self._scan_workspace_skills())
        # Plugin-shipped skills (content.skills contributions) follow the
        # workspace ones; a workspace skill of the same name wins so a user's
        # own copy (or a disabled marker) is never shadowed by a plugin.
        taken = {s.name for s in skills}
        for owner, spec in plugin_skills():
            try:
                info = self._parse_skill_md(spec.manifest_path)
            except Exception as exc:  # noqa: BLE001 — one bad plugin skill must not drop the table
                logger.warning(f"[skills] plugin {owner}: cannot parse {spec.manifest_path}: {exc}")
                continue
            if info.name in taken:
                continue
            taken.add(info.name)
            skills.append(info.model_copy(update={"source_type": "plugin", "builtin": False}))
        return skills

    def _scan_workspace_skills(self) -> List[SkillInfo]:
        assert self.skills_dir is not None
        skills = []
        for skill_path in sorted(self.skills_dir.iterdir(), key=lambda p: p.name):
            if skill_path.is_dir() and not skill_path.name.startswith("."):
                # A directory without SKILL.md (e.g. auto-created by the
                # agent) is still listed: _parse_skill_md falls back to the
                # directory name + whatever .skill_meta.json carries
                # (description, study state, studied requirements).
                skills.append(self._parse_skill_md(skill_path / "SKILL.md"))

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

        env_pattern = re.compile(r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b")
        candidates = set(env_pattern.findall(text))
        env_suffixes = (
            "_KEY",
            "_TOKEN",
            "_SECRET",
            "_ACCOUNT",
            "_URL",
            "_PASSWORD",
            "_PASS",
            "_API",
            "_AUTH",
            "_CREDENTIAL",
        )
        env_prefixes = (
            "API_",
            "AWS_",
            "GOOGLE_",
            "OPENAI_",
            "ANTHROPIC_",
            "TAVILY_",
            "GOG_",
            "GITHUB_",
            "SLACK_",
            "DISCORD_",
        )
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

        # Read .skill_meta.json (if exists). Coerced to a dict so a malformed,
        # agent-writable meta cannot crash the .get() chain below (which runs
        # OUTSIDE the frontmatter try) and take down _scan_skills with it.
        meta_data = self._load_meta_dict(skill_dir / ".skill_meta.json")

        # Extract fields from meta_data
        source_url = meta_data.get("source_url")
        source_type = meta_data.get("source_type")
        installed_at = meta_data.get("installed_at")
        builtin = bool(meta_data.get("builtin", False))
        study_fields = {
            "study_status": meta_data.get("study_status"),
            "study_result": meta_data.get("study_result"),
            "study_error": meta_data.get("study_error"),
            "studied_at": meta_data.get("studied_at"),
        }

        # Extract requirements from .skill_meta.json (may have been set by study)
        meta_requires = meta_data.get("requires", {})
        meta_requires_env = meta_requires.get("env", []) if isinstance(meta_requires, dict) else []
        meta_requires_bins = meta_requires.get("bins", []) if isinstance(meta_requires, dict) else []

        # Read env_config to determine if all required vars are configured
        env_config = meta_data.get("env_config", {})

        try:
            # A directory without SKILL.md (agent-created, or meta written
            # before the manifest landed) is the meta-only fallback below,
            # not a parse failure worth a warning.
            content = skill_md.read_text(encoding="utf-8") if skill_md.exists() else ""
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    fm = parts[1]
                    meta = yaml.safe_load(fm)
                    if meta:
                        # Parse structured metadata (the OpenClaw skill format and its aliases)
                        fm_requires_env = []
                        fm_requires_bins = []
                        metadata_field = meta.get("metadata", {})
                        if isinstance(metadata_field, str):
                            try:
                                metadata_field = json.loads(metadata_field)
                            except (json.JSONDecodeError, TypeError):
                                metadata_field = {}
                        if isinstance(metadata_field, dict):
                            requires = _skill_runtime_requires(metadata_field)
                            fm_requires_env = requires.get("env", [])
                            fm_requires_bins = requires.get("bins", [])

                        # Body scan is a FALLBACK for skills that declare
                        # nothing. When the frontmatter explicitly declares
                        # requires.env, trust it — the body often mentions
                        # OPTIONAL override vars (e.g. NETMIND_BASE_URL) that
                        # must not be promoted to "required".
                        if fm_requires_env:
                            body_env = []
                        else:
                            body_text = parts[2] if len(parts) >= 3 else ""
                            body_env = self._extract_env_vars_from_text(body_text)

                        # Merge frontmatter + body scan + meta.json requirements (union)
                        requires_env = sorted(set(fm_requires_env + body_env + meta_requires_env)) or None
                        requires_bins = sorted(set(fm_requires_bins + meta_requires_bins)) or None

                        # Check if all required env vars are configured — a
                        # required var counts only if its stored value decrypts
                        # (or is platform-resolved), so a credential under a
                        # lost key reads as NOT configured (2026-08-01).
                        # platform_assumed carries the platform-only half down
                        # so the API layer can DB-validate it without re-reading
                        # meta (see env_config_status / _enrich_platform_env_status).
                        env_configured, env_platform_assumed = env_config_status(
                            requires_env, env_config
                        )

                        return SkillInfo(
                            name=meta.get("name", skill_dir.name),
                            description=meta.get("description", ""),
                            path=str(skill_dir),
                            builtin=builtin,
                            version=meta.get("version"),
                            author=meta.get("author"),
                            source_url=source_url,
                            source_type=source_type,
                            installed_at=installed_at,
                            requires_env=requires_env,
                            requires_bins=requires_bins,
                            env_configured=env_configured,
                            env_platform_assumed=env_platform_assumed,
                            **study_fields,
                        )
        except Exception as e:
            logger.warning(f"Failed to parse SKILL.md at {skill_md}: {e}")

        # Fallback: use directory name as the name
        requires_env = sorted(set(meta_requires_env)) or None
        requires_bins = sorted(set(meta_requires_bins)) or None
        env_configured, env_platform_assumed = env_config_status(
            requires_env, env_config
        )

        # A SKILL.md that exists but has no parseable frontmatter keeps the
        # long-standing empty description; only a directory WITHOUT a
        # SKILL.md borrows the meta description (or the placeholder).
        description = "" if skill_md.exists() else (meta_data.get("description") or "(No SKILL.md found)")
        return SkillInfo(
            name=skill_dir.name,
            description=description,
            path=str(skill_dir),
            builtin=builtin,
            source_url=source_url,
            source_type=source_type,
            installed_at=installed_at,
            requires_env=requires_env,
            requires_bins=requires_bins,
            env_configured=env_configured,
            env_platform_assumed=env_platform_assumed,
            **study_fields,
        )

    def _save_skill_meta(self, skill_dir: Path, source_url: Optional[str] = None, source_type: str = "unknown") -> None:
        """Save Skill metadata to .skill_meta.json.

        Preserves any metadata that travelled with the skill (``env_config`` /
        ``study_result`` / ``requires`` from a bundle full_copy archive) —
        only the provenance fields are (re)written here. A fresh install (github
        clone / plain zip with no meta) has nothing to preserve, so this is a
        no-op merge for those paths.
        """
        meta_file = skill_dir / ".skill_meta.json"
        meta_data: dict = {}
        if meta_file.exists():
            try:
                meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
                if not isinstance(meta_data, dict):
                    meta_data = {}
            except Exception:
                meta_data = {}
        meta_data.update(
            {
                "source_url": source_url,
                "source_type": source_type,
                "installed_at": datetime.now().isoformat(),
            }
        )
        try:
            meta_file.write_text(json.dumps(meta_data, indent=2, ensure_ascii=False), encoding="utf-8")
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
                meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
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
                meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
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
            meta_file.write_text(json.dumps(meta_data, indent=2, ensure_ascii=False), encoding="utf-8")
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
        # Mirror the guard already used by `_scan_skills()` — the skills
        # directory may be absent (never created, deleted, or the agent has
        # no user_id). `iterdir()` would raise FileNotFoundError below.
        if not self.skills_dir or not self.skills_dir.exists():
            return None
        # Direct match — only for a name that is a single safe path segment.
        # The name may come from SKILL.md frontmatter the agent wrote
        # (``org/pdf``, ``.x``); such a name must not be joined onto the
        # skills dir, but it can still be matched by the scan below, which
        # only ever returns real directories from ``iterdir``.
        try:
            safe_id_segment(skill_name, label="skill name")
        except ValueError:
            direct = None
        else:
            direct = self.skills_dir / skill_name
        if direct is not None and direct.exists() and direct.is_dir():
            return direct
        # Scan and match by parsed name from SKILL.md. Name-sorted (R4d) so
        # that when two directories declare the same frontmatter name the
        # winner is deterministic instead of readdir-order dependent — the
        # resolved dir decides which .skill_meta.json (env config, study
        # status) is read and written.
        for skill_path in sorted(self.skills_dir.iterdir(), key=lambda p: p.name):
            if skill_path.is_dir() and not skill_path.name.startswith("."):
                skill_md = skill_path / "SKILL.md"
                if skill_md.exists():
                    try:
                        content = skill_md.read_text(encoding="utf-8")
                        if content.startswith("---"):
                            parts = content.split("---", 2)
                            if len(parts) >= 3:
                                meta = yaml.safe_load(parts[1])
                                if meta and meta.get("name") == skill_name:
                                    return skill_path
                    except Exception:
                        pass
        return None

    @staticmethod
    def _load_meta_dict(meta_file: Path) -> dict:
        """Read a .skill_meta.json into a dict, coercing away malformed shapes.

        The file is agent-writable, so it may be absent, non-JSON, or valid JSON
        that is not an object (an array / string / number). Every one of those
        yields ``{}`` — a malformed meta must never crash ``_parse_skill_md`` /
        ``_scan_skills`` / the credential-injection path (the "malformed meta
        must not crash the panel or drop the agent's skills" contract). The
        non-object case is logged (with the path) rather than swallowed, so a
        corrupt file leaves a breadcrumb instead of silently reading as empty.
        """
        if not meta_file.exists():
            return {}
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 — corrupt/non-JSON agent-written file
            logger.warning(f"Failed to read {meta_file}: {e}")
            return {}
        if isinstance(data, dict):
            return data
        logger.warning(f".skill_meta.json is not a JSON object, ignoring: {meta_file}")
        return {}

    def _read_skill_meta(self, skill_name: str) -> dict:
        """Read .skill_meta.json for a skill, returns empty dict if not found"""
        skill_dir = self._resolve_skill_dir(skill_name)
        if not skill_dir:
            return {}
        return self._load_meta_dict(skill_dir / ".skill_meta.json")

    def _write_skill_meta(self, skill_name: str, meta_data: dict) -> None:
        """Write .skill_meta.json for a skill"""
        skill_dir = self._resolve_skill_dir(skill_name)
        if not skill_dir:
            logger.warning(f"Cannot write .skill_meta.json: skill dir not found for '{skill_name}'")
            return
        meta_file = skill_dir / ".skill_meta.json"
        try:
            meta_file.write_text(json.dumps(meta_data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to write .skill_meta.json for '{skill_name}': {e}")

    def get_skill_requirements(self, skill_name: str) -> Optional[dict]:
        """Resolve a skill's runtime requirements — ``{"env": [...], "bins": [...]}``,
        or None when no such skill is installed (a caller must not read
        "unknown skill" as "nothing to configure").

        This is the ONE resolver behind every "what does this skill need"
        surface: the Skills panel / routes read ``SkillInfo.requires_env``
        from ``_parse_skill_md`` and the ``skill_list_required_env`` MCP
        tool calls this method, which delegates to the same parser. It used
        to read only ``.skill_meta.json["requires"]`` — a field the study
        step writes — so an installed-but-never-studied skill answered the
        agent "no required environment variables" while the UI listed the
        vars declared in the SKILL.md frontmatter / mentioned in its body
        (GitHub #115). Frontmatter, body scan (fallback) and studied meta are
        unioned exactly as the UI shows them.
        """
        skill_dir = self._resolve_skill_dir(skill_name)
        if skill_dir is None:
            return None
        info = self._parse_skill_md(skill_dir / "SKILL.md")
        return {
            "env": list(info.requires_env or []),
            "bins": list(info.requires_bins or []),
        }

    def get_skill_env_config(self, skill_name: str) -> dict:
        """Get env_config from .skill_meta.json (var_name -> base64-encoded value)"""
        meta_data = self._read_skill_meta(skill_name)
        return meta_data.get("env_config", {})

    def set_skill_env_config(self, skill_name: str, env_config: dict) -> None:
        """Save env var values to .skill_meta.json (Fernet encrypted)."""
        from narranexus.platform.marketplace import get_secret_box

        meta_data = self._read_skill_meta(skill_name)

        box = get_secret_box()
        encoded_config = {key: box.encrypt(value) for key, value in env_config.items() if value}

        # Merge with existing (allow partial updates)
        existing = meta_data.get("env_config", {})
        existing.update(encoded_config)
        meta_data["env_config"] = existing

        self._write_skill_meta(skill_name, meta_data)
        logger.info(f"Saved env config for skill '{skill_name}': {list(env_config.keys())}")

    def update_requirements(self, skill_name: str, env_list: list, bins_list: list) -> None:
        """Merge new requirements into .skill_meta.json (union with existing)"""
        meta_data = self._read_skill_meta(skill_name)
        existing = meta_data.get("requires", {})
        existing_env = existing.get("env", []) if isinstance(existing, dict) else []
        existing_bins = existing.get("bins", []) if isinstance(existing, dict) else []

        merged_env = sorted(set(existing_env + env_list))
        merged_bins = sorted(set(existing_bins + bins_list))

        meta_data["requires"] = {
            "env": merged_env,
            "bins": merged_bins,
        }
        self._write_skill_meta(skill_name, meta_data)
        logger.info(f"Updated requirements for skill '{skill_name}': env={merged_env}, bins={merged_bins}")

    async def _resolve_platform_env(self, env: dict, skills: list) -> dict:
        """Runtime-only injection of platform-resolvable env vars.

        Skills may declare requirements (e.g. NETMIND_API_KEY) that the
        platform can satisfy from the user's provider configuration instead
        of asking the user to re-enter a credential. Resolution happens per
        run, per user, and the value is NEVER persisted to the workspace —
        so key rotation applies immediately and cloud workspaces hold no
        extra key copies. An explicit skill env_config value always wins.
        """
        needed = [
            v for v in PLATFORM_RESOLVED_ENV
            if not env.get(v) and any(v in (s.requires_env or []) for s in skills)
        ]
        if not needed or self.db is None or not self.user_id:
            return env
        for var in needed:
            if var == "NETMIND_API_KEY":
                try:
                    row = await self.db.get_one(
                        "user_providers",
                        {"user_id": self.user_id, "source": "netmind", "protocol": "openai"},
                    )
                    if row and row.get("api_key"):
                        env[var] = row["api_key"]
                        logger.debug("Injected NETMIND_API_KEY from user's provider config")
                except Exception as e:
                    logger.warning(f"Platform env resolve failed for {var}: {e}")
        return env

    def merge_skill_meta(self, skill_name: str, fields: dict) -> None:
        """Merge fields into a skill's .skill_meta.json (public, for the
        InstallPipeline's lock/audit step and config migration)."""
        meta_data = self._read_skill_meta(skill_name)
        meta_data.update(fields)
        self._write_skill_meta(skill_name, meta_data)

    def read_skill_meta(self, skill_name: str) -> dict:
        """Public read of a skill's .skill_meta.json (empty dict if absent)."""
        return self._read_skill_meta(skill_name)

    def parse_skill_package(self, skill_root: Path) -> SkillInfo:
        """Parse a staged (not yet installed) skill directory's SKILL.md."""
        return self._parse_skill_md(skill_root / "SKILL.md")

    def get_all_skill_env_vars(self) -> dict:
        """
        Collect all configured env vars from all enabled skills.
        Returns a merged dict of plaintext env var name -> value.

        Legacy plain-base64 values (pre-Fernet format) are decrypted
        transparently and re-persisted encrypted on first read.

        A value that is ciphertext this key cannot decrypt (the SecretBox key
        rotated or was lost) is SKIPPED, never injected as ciphertext — the
        skill fails cleanly for a missing var instead of running with garbage
        (2026-08-01). Legacy re-persist is skipped whenever any value in the
        same skill failed, so we don't overwrite the still-recoverable
        ciphertext of the failed one.
        """
        from narranexus.platform.marketplace import get_secret_box

        try:
            box = get_secret_box()
        except Exception as e:  # noqa: BLE001 — bad SKILL_SECRETS_KEY / unwritable dir
            # A process-level key failure must not raise out of gather
            # (it would drop this agent's whole skills contribution). Fail CLOSED:
            # inject nothing, and emit the single loud ops signal here.
            logger.error(f"SecretBox unavailable; skipping ALL skill credential injection: {e}")
            return {}
        all_env = {}
        skills = self._scan_skills()
        for skill in skills:
            meta_data = self._read_skill_meta(skill.name)
            env_config = meta_data.get("env_config", {})
            if not env_config:
                continue
            plain, needs_rewrite, failed = box.decrypt_env_config(env_config)
            if failed:
                # Undecryptable creds (key rotated/lost) are SKIPPED, never
                # injected as ciphertext — the skill fails cleanly for a
                # missing var instead of running with garbage (2026-08-01).
                # env_configured turns False for these (see backend/routes/
                # skills.py) so the UI prompts the user to re-enter them.
                logger.error(
                    f"Skill '{skill.name}': {len(failed)} credential(s) cannot "
                    f"be decrypted and were skipped — re-enter {failed}"
                )
            if needs_rewrite:
                # Re-persist ONLY the decryptable values (legacy-migration);
                # never overwrite the meta while some values failed to decrypt,
                # or we'd destroy the still-encrypted (recoverable-with-the-old-
                # key) ciphertext of the failed ones.
                if not failed:
                    meta_data["env_config"] = box.encrypt_env_config(plain)
                    self._write_skill_meta(skill.name, meta_data)
                    logger.info(f"Migrated legacy env config to encrypted form for skill '{skill.name}'")
            for key, value in plain.items():
                if key in all_env and all_env[key] != value:
                    logger.warning(f"Env var '{key}' conflict: skill '{skill.name}' overrides previous value")
                all_env[key] = value
        return all_env

    # =========================================================================
    # Skill Management Methods (called by API layer)
    # =========================================================================

    def list_skills(self, include_disabled: bool = False) -> List[SkillInfo]:
        """List all Skills.

        Materializes built-in skills first so the API/UI surface them for a
        freshly-created agent that has never run (materialize is otherwise only
        triggered by gather on the first run).
        """
        self._materialize_builtin_skills()
        skills = self._scan_skills()

        if include_disabled and self.skills_dir:
            disabled_dir = self.skills_dir / ".disabled"
            if disabled_dir.exists():
                # Name-sorted (R4d): this list is API/UI-facing, and the
                # enabled half above is already deterministic.
                for skill_path in sorted(disabled_dir.iterdir(), key=lambda p: p.name):
                    if skill_path.is_dir():
                        skill_md = skill_path / "SKILL.md"
                        if skill_md.exists():
                            info = self._parse_skill_md(skill_md)
                            info.disabled = True
                            skills.append(info)

        return skills

    def install_skill(self, zip_file_path: Path, target_dir_name: Optional[str] = None) -> SkillInfo:
        """Install Skill from zip file.

        ``target_dir_name`` pins the destination folder name under ``skills/``.
        Bundle import passes the manifest's known ``skill_dir`` here so a
        full_copy skill lands in ``skills/<skill_dir>/`` and overwrites the
        workspace-snapshot copy — instead of being re-derived from the SKILL.md
        frontmatter ``name``, which falls back to the extraction temp-dir
        basename when the SKILL.md has no frontmatter (which used to leave a
        stray ``skills/tmpXXXX/`` disjoint from the real skill dir).
        """
        if not self.skills_dir:
            raise ValueError("skills_dir is not configured (user_id is required)")

        # Extract to temp directory for validation first
        temp_dir = Path(tempfile.mkdtemp())
        try:
            skill_root = self.extract_skill_package(zip_file_path, temp_dir)
            return self.install_from_dir(
                skill_root, source_type="zip", target_dir_name=target_dir_name
            )
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    def extract_skill_package(self, zip_file_path: Path, dest_dir: Path) -> Path:
        """Safely extract a skill zip into dest_dir and locate the skill root.

        Public so the InstallPipeline can stage a package, security-scan it,
        and only then commit it via install_from_dir. Same validation and
        error messages as the classic one-shot install_skill path.
        """
        self._extract_zip_safely(zip_file_path, dest_dir)
        skill_root = self._find_skill_root(dest_dir)
        if not skill_root:
            raise ValueError(
                f"Invalid skill package: SKILL.md not found. {self.SKILL_LAYOUT_HINT}"
            )
        return skill_root

    def install_from_dir(
        self,
        skill_root: Path,
        source_type: str,
        source_url: Optional[str] = None,
        target_dir_name: Optional[str] = None,
    ) -> SkillInfo:
        """Commit a staged skill directory into skills/<name>/.

        The shared tail of every install path (zip / github / marketplace):
        parse SKILL.md, derive the destination name, replace any existing
        directory, move into place, write provenance metadata.
        """
        if not self.skills_dir:
            raise ValueError("skills_dir is not configured (user_id is required)")

        self.skills_dir.mkdir(parents=True, exist_ok=True)

        skill_md = skill_root / "SKILL.md"
        if not skill_md.exists():
            raise ValueError("Invalid skill package: SKILL.md not found.")
        info = self._parse_skill_md(skill_md)

        # An explicit target_dir_name (e.g. the bundle's known skill_dir)
        # wins over the SKILL.md-derived name.
        safe_skill_name = sanitize_filename(target_dir_name or info.name, label="skill name")
        target_dir = ensure_within_directory(self.skills_dir, safe_skill_name, label="skill name")
        if target_dir.exists():
            shutil.rmtree(target_dir)

        shutil.move(str(skill_root), str(target_dir))

        self._save_skill_meta(target_dir, source_url=source_url, source_type=source_type)

        info.name = safe_skill_name
        info.path = str(target_dir)
        info.source_url = source_url
        info.installed_at = datetime.now().isoformat()
        logger.info(f"Installed skill '{info.name}' to {target_dir} (source={source_type})")
        return info

    # Layouts every install source (zip / GitHub clone) understands. One
    # message, quoted by both rejection paths, so the user always learns
    # where SKILL.md is expected.
    SKILL_LAYOUT_HINT = (
        "Place SKILL.md at the root, inside a single subfolder "
        "(<name>/SKILL.md), or one per skill under skills/<name>/SKILL.md."
    )

    # A repository is installed whole; this caps how many skills one URL
    # (user- or agent-supplied — `skill_install` is an MCP tool) can drop
    # into a workspace in one request. Larger repos need a fork / subset.
    MAX_SKILLS_PER_REPO = 20

    def find_skill_roots(self, staged_dir: Path) -> List[Path]:
        """Every skill directory inside a staged package or clone, name-sorted.

        Shared by the zip and GitHub paths (GitHub #95: the clone path used
        to hardcode ``<clone>/SKILL.md`` while zip already looked one level
        down). Accepted layouts:

        - ``SKILL.md`` at the root -> that single skill (nothing else is
          searched; a root manifest owns the whole tree);
        - ``<name>/SKILL.md`` for any top-level directory -> one skill each;
        - ``skills/<name>/SKILL.md`` (the agent-skills / plugin repo layout)
          -> one skill each.

        Dot-directories (``.git``, ``.github``) are skipped. Name-sorted
        (R4d) so a multi-skill repo resolves to the same order on every
        machine, not to whatever readdir happened to yield first. More than
        MAX_SKILLS_PER_REPO roots is a ValueError (before anything is
        installed).
        """
        if not staged_dir.is_dir():
            return []
        if (staged_dir / "SKILL.md").exists():
            return [staged_dir]

        roots: List[Path] = []
        for subdir in sorted(staged_dir.iterdir(), key=lambda p: p.name):
            if not subdir.is_dir() or subdir.name.startswith("."):
                continue
            if (subdir / "SKILL.md").exists():
                roots.append(subdir)
            elif subdir.name == "skills":
                for nested in sorted(subdir.iterdir(), key=lambda p: p.name):
                    if nested.is_dir() and not nested.name.startswith(".") and (nested / "SKILL.md").exists():
                        roots.append(nested)
        if len(roots) > self.MAX_SKILLS_PER_REPO:
            raise ValueError(
                f"This package contains {len(roots)} skills; at most "
                f"{self.MAX_SKILLS_PER_REPO} can be installed from one repository or zip. "
                "Install a fork or a subset that ships only the skills you need."
            )
        return roots

    def _find_skill_root(self, extract_dir: Path) -> Optional[Path]:
        """The single skill root of a zip package (first of find_skill_roots).

        A zip is one skill by contract; when an archive happens to carry
        several, the name-sorted first one wins deterministically — up to
        MAX_SKILLS_PER_REPO, beyond which the whole archive is rejected
        like a repository would be (the cap is on the package, not the
        source).
        """
        roots = self.find_skill_roots(extract_dir)
        return roots[0] if roots else None

    def _extract_zip_safely(self, zip_file_path: Path, target_dir: Path) -> None:
        """Extract a skill archive while rejecting zip-slip style paths."""
        # Shared with the admission gate that lets archives in
        # (`bundle/security.validate_skill_archive_*`). If these two disagree,
        # an archive can be stored and exported and then fail here at install
        # time — see the constants' definition for why they live in one place.
        # Module attribute, not a from-import: same value the admission gate
        # reads, resolved at call time so the two can never diverge.
        max_entries = _file_safety.MAX_SKILL_ARCHIVE_ENTRIES
        max_uncompressed_bytes = _file_safety.MAX_SKILL_ARCHIVE_DECOMPRESSED_BYTES
        max_uncompressed_mb = max_uncompressed_bytes // (1024 * 1024)

        try:
            zip_ref_cm = zipfile.ZipFile(zip_file_path, "r")
        except (zipfile.BadZipFile, OSError) as e:
            # Not a valid zip at all (corrupted upload, wrong file type), or
            # not an openable file (a directory, an unreadable path). Every
            # other rejection in this method raises ValueError, which the
            # install route maps to a 400 (`except ValueError: raise
            # _reject(...)`). BadZipFile and OSError are plain Exceptions,
            # not ValueErrors, so left unconverted they fell through to the
            # route's generic `except Exception` branch and surfaced as an
            # opaque 500. OSError is caught on the OPEN only — the same
            # shape as bundle/security.validate_skill_archive_path, whose
            # comment records that catching BadZipFile alone let those
            # escape; an OSError from a read inside the `with` below is a
            # genuine server fault and must stay one.
            raise ValueError(f"Invalid skill package: not a valid zip file ({e}).") from e

        with zip_ref_cm as zip_ref:
            members = zip_ref.infolist()
            if len(members) > max_entries:
                raise ValueError(
                    f"Invalid skill package: too many files ({len(members)} entries, limit is {max_entries})."
                )

            total_uncompressed = 0
            target_root = target_dir.resolve(strict=False)
            for member in members:
                member_path = validate_zip_member_path(member.filename)
                total_uncompressed += member.file_size
                if total_uncompressed > max_uncompressed_bytes:
                    raise ValueError(
                        f"Invalid skill package: uncompressed size exceeds the {max_uncompressed_mb} MB limit."
                    )

                destination = (target_dir / member_path).resolve(strict=False)
                if target_root not in destination.parents and destination != target_root:
                    raise ValueError(
                        f"Invalid skill package: path traversal not allowed (offending entry: {member.filename!r})."
                    )

                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue

                destination.parent.mkdir(parents=True, exist_ok=True)
                with zip_ref.open(member) as src, open(destination, "wb") as dst:
                    shutil.copyfileobj(src, dst)

    def install_from_github(self, url: str, branch: str = "main") -> List[SkillInfo]:
        """
        Install every skill a GitHub repository ships.

        Args:
            url: GitHub repository URL, supported formats:
                 - https://github.com/user/repo
                 - github:user/repo (shorthand)
            branch: Branch name, defaults to main

        Returns:
            The installed SkillInfo list — one entry for a single-skill repo
            (root or nested SKILL.md), one per skill for a multi-skill repo
            (see find_skill_roots for the accepted layouts). Never empty:
            a repo without any SKILL.md raises ValueError.
        """
        if not self.skills_dir:
            raise ValueError("skills_dir is not configured (user_id is required)")

        # Clone to temp directory, then commit each root via the shared tail
        temp_dir = Path(tempfile.mkdtemp())
        try:
            skill_roots, canonical_url = self.fetch_github_repo(url, branch, temp_dir)
            return [
                self.install_from_dir(root, source_type="github", source_url=canonical_url)
                for root in skill_roots
            ]
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    def fetch_github_repo(self, url: str, branch: str, dest_dir: Path) -> tuple[List[Path], str]:
        """Validate a GitHub URL and shallow-clone it into dest_dir.

        Public so the InstallPipeline can stage a repo, security-scan it, and
        only then commit it. Returns (skill_roots, canonical_url) where
        skill_roots is the non-empty, name-sorted list from find_skill_roots.
        """
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
        _validate_git_branch(branch)

        logger.info(f"Cloning {url} (branch: {branch}) to {dest_dir}")
        try:
            # "--" ends option parsing: url / dest can never be read as options.
            subprocess.run(
                ["git", "clone", "--depth", "1", "-b", branch, "--", url, str(dest_dir)],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            # git stderr can carry resolved hosts / internal detail — log only.
            logger.warning(f"git clone failed for {url} (branch {branch}): {e.stderr}")
            raise ValueError(
                "Failed to clone the repository — check that the URL and branch exist "
                "and the repository is public"
            ) from e

        # Remove .git directory (version control not needed)
        git_dir = dest_dir / ".git"
        if git_dir.exists():
            shutil.rmtree(git_dir)

        skill_roots = self.find_skill_roots(dest_dir)
        if not skill_roots:
            raise ValueError(
                f"Invalid skill: no SKILL.md found in {url} (branch {branch}). "
                f"{self.SKILL_LAYOUT_HINT}"
            )
        return skill_roots, url

    @staticmethod
    def _dir_is_builtin(skill_dir: Path) -> bool:
        """True if a skill directory is a built-in (per its .skill_meta.json).

        Thin delegate to the single source of truth in ``bundle.skill_secrets``
        so the built-in flag semantics stay identical across the module, the
        backup path, and the export builder.
        """
        from narranexus.platform.bundle.skill_secrets import dir_is_builtin

        return dir_is_builtin(skill_dir)

    def remove_skill(self, skill_name: str) -> bool:
        """Remove a Skill.

        Raises:
            ValueError: If the skill is built-in — built-ins ship with the app
                and cannot be deleted (they would re-materialize anyway). Disable
                it instead.
        """
        safe_id_segment(skill_name, label="skill name")
        if not self.skills_dir:
            return False

        skill_path = self.skills_dir / skill_name
        disabled_path = self.skills_dir / ".disabled" / skill_name
        if self._dir_is_builtin(skill_path) or self._dir_is_builtin(disabled_path):
            raise ValueError(f"Skill '{skill_name}' is built-in and can't be removed. Disable it instead.")

        if skill_path.exists():
            shutil.rmtree(skill_path)
            logger.info(f"Removed skill '{skill_name}' from {skill_path}")
            return True

        # Also check disabled directory
        if disabled_path.exists():
            shutil.rmtree(disabled_path)
            logger.info(f"Removed disabled skill '{skill_name}' from {disabled_path}")
            return True

        return False

    def disable_skill(self, skill_name: str) -> bool:
        """Disable a Skill"""
        safe_id_segment(skill_name, label="skill name")
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
        safe_id_segment(skill_name, label="skill name")
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
