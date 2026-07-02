"""
@file_name: arena_onboarding.py
@author: Bin Liang
@date: 2026-06-15
@description: Server-side onboarding of an Agent onto the NetMind Agent Arena
             (arena42.ai). One class registers an Arena identity via the public
             API and lays the "arena" skill (SKILL.md + credentials) into a
             given workspace, so a caller can do the whole thing by passing a
             workspace path — no LLM tool-calls, no arena-cli, no DB coupling.

Why this exists
---------------
An Agent registering itself through Arena's `skill.md` flow costs minutes of
LLM tool calls. Arena registration is actually a single sub-second HTTP call;
the cost is the agent doing it interactively. This utility moves registration
+ skill installation to the server so a provisioned Agent lands ready to play.

We deliberately use the **direct API**, not `@netmind/arena-cli`:
  - the CLI stores to a machine-global `~/.config/arena/credentials.json`
    (single-agent assumption) — wrong for our multi-tenant cloud where many
    agents share one host;
  - this class writes per-agent into the agent's own workspace, fully isolated.

Arena naming rules (verified 2026-06-15):
  - name must match [A-Za-z0-9_]; other chars → HTTP 400 VALIDATION_ERROR;
  - names are globally unique → duplicate → HTTP 409 NAME_TAKEN;
  - `referralCode` is optional — no partner secret needed; register grants
    200 credits.

The written `.skill_meta.json` matches the SkillModule format exactly
(env_config values base64-encoded), so the running agent's SkillModule reads
the credentials back via `get_all_skill_env_vars()` with no extra wiring.
"""

from __future__ import annotations

import base64
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

import httpx

# ── Nintendo-style three-group gamertag word lists ──────────────────────────
# 24 × 24 × 24 = 13,824 base combinations; a numeric suffix on collision
# extends that to ~1.38M. Tokens are single words, [A-Za-z] only.

GROUP_TEMPERAMENT = (
    "Brave", "Swift", "Clever", "Mighty", "Silent", "Fierce", "Bold", "Sly",
    "Stoic", "Nimble", "Savage", "Lucid", "Radiant", "Relentless", "Vivid",
    "Crafty", "Daring", "Steady", "Witty", "Zealous", "Cunning", "Gallant",
    "Keen", "Valiant",
)
GROUP_FORCE = (
    "Thunder", "Shadow", "Frost", "Blaze", "Storm", "Ember", "Echo", "Nova",
    "Quantum", "Tempest", "Cinder", "Glacier", "Mirage", "Comet", "Aurora",
    "Vortex", "Onyx", "Solar", "Lunar", "Plasma", "Granite", "Zephyr",
    "Titan", "Phantom",
)
GROUP_CREATURE = (
    "Falcon", "Tiger", "Dragon", "Wolf", "Phoenix", "Raven", "Panther",
    "Cobra", "Lynx", "Orca", "Griffin", "Viper", "Jaguar", "Heron", "Mantis",
    "Stag", "Kraken", "Bison", "Osprey", "Sable", "Fox", "Hawk", "Ronin",
    "Sphinx",
)
BASE_NAME_COMBINATIONS = len(GROUP_TEMPERAMENT) * len(GROUP_FORCE) * len(GROUP_CREATURE)

DEFAULT_API_BASE = "https://api.arena42.ai"
DEFAULT_SKILL_MD_URL = "https://arena42.ai/skill.md"
DEFAULT_SKILL_VERSION = "1.23.0"
DEFAULT_SKILL_NAME = "arena"

# --- Auth / CLI-override directive: SINGLE SOURCE OF TRUTH -------------------
# Arena's doc pushes the `npx arena` CLI everywhere, but the CLI reads a
# machine-global ~/.config/arena/credentials.json — wrong on our shared,
# multi-tenant host. This directive is the canonical rule. It is the AUTHORITY
# in the agent's awareness (always in the prompt, survives skill refresh) AND is
# regenerated into the installed SKILL.md header on every (re)install, so an
# Arena skill update never drops it. Marked so a future migration can replace
# just this block inside awareness without clobbering the rest.
ARENA_AUTH_MARKER_START = "<!-- arena-auth-directive:start -->"
ARENA_AUTH_MARKER_END = "<!-- arena-auth-directive:end -->"


def arena_auth_directive(gamertag: str) -> str:
    """Canonical auth/CLI-override rule (single source; used by awareness + skill)."""
    return (
        f"{ARENA_AUTH_MARKER_START}\n"
        "HOW TO AUTHENTICATE — this overrides any CLI guidance in the arena skill\n"
        "- Use the DIRECT HTTP API only. Your credentials are injected as env vars:\n"
        "  ARENA_API_KEY (your key) and ARENA_API_URL (base URL); they are also in\n"
        "  skills/arena/credentials.json. Always send\n"
        "  `Authorization: Bearer $ARENA_API_KEY`.\n"
        "- NEVER use the arena CLI (`npx arena ...`). On this shared host the CLI\n"
        "  reads a global ~/.config/arena/credentials.json that belongs to a\n"
        "  DIFFERENT agent — you would act as the wrong agent (wrong name/credits).\n"
        f"- You are already registered as {gamertag}; never register again. If any\n"
        f"  check shows a name that is not {gamertag}, you used the wrong credentials\n"
        "  — stop and re-issue the call with $ARENA_API_KEY.\n"
        f"{ARENA_AUTH_MARKER_END}"
    )


def skill_md_override_header(gamertag: str) -> str:
    """The same directive, markdown-blockquoted, prepended to the installed SKILL.md.

    Regenerated on every install so an Arena skill.md update never drops it (the
    header is our mechanism's output, not part of Arena's fetched content).
    """
    quoted = "\n".join(
        ("> " + ln) if ln else ">" for ln in arena_auth_directive(gamertag).splitlines()
    )
    return (
        "> ## ⚠️ NarraNexus override — READ FIRST (supersedes the CLI steps below)\n"
        ">\n"
        f"{quoted}\n\n"
        "---\n\n"
    )


class ArenaNameExhausted(RuntimeError):
    """Raised when no free Arena name could be found after all attempts."""


class ArenaApiError(RuntimeError):
    """Raised on an unexpected Arena API response (not 201 / not 409)."""


@dataclass
class ArenaCredentials:
    """Credentials returned by Arena registration."""

    api_key: str
    agent_id: str
    agent_name: str
    claim_token: Optional[str] = None
    claim_url: Optional[str] = None
    referral_code: Optional[str] = None

    def as_credentials_json(self) -> Dict[str, Optional[str]]:
        """Shape Arena's own `credentials.json` expects, plus the claim token."""
        return {
            "api_key": self.api_key,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "claim_token": self.claim_token,
        }


@dataclass
class ArenaOnboardResult:
    """Outcome of a full onboard: credentials + where the skill landed."""

    credentials: ArenaCredentials
    skill_dir: Path
    files_written: List[str] = field(default_factory=list)


class ArenaOnboarder:
    """
    Register an Agent on Arena and install the configured "arena" skill into a
    workspace.

    Typical use from another script::

        from xyz_agent_context.utils.arena_onboarding import ArenaOnboarder

        onboarder = ArenaOnboarder()
        result = onboarder.onboard("/path/to/agent_xxx_user_yyy")
        print(result.credentials.agent_name, result.skill_dir)

    The class holds no DB or settings dependency — give it a workspace path and
    it does registration + file layout. Inject `http_client` / `rng` for tests.
    """

    def __init__(
        self,
        *,
        api_base: str = DEFAULT_API_BASE,
        skill_md_url: str = DEFAULT_SKILL_MD_URL,
        skill_version: str = DEFAULT_SKILL_VERSION,
        referral_code: Optional[str] = None,
        timeout: float = 30.0,
        http_client: Optional[httpx.Client] = None,
        rng: Optional[random.Random] = None,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.skill_md_url = skill_md_url
        self.skill_version = skill_version
        self.referral_code = referral_code
        self.timeout = timeout
        self._client = http_client
        self._owns_client = http_client is None
        self._rng = rng or random.Random()

    # ── HTTP plumbing ───────────────────────────────────────────────────────

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "X-Arena-Skill-Version": self.skill_version,
            "Content-Type": "application/json",
        }

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "ArenaOnboarder":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── Name generation ─────────────────────────────────────────────────────

    def generate_name(self) -> str:
        """One random three-group gamertag, e.g. 'Brave_Thunder_Falcon'."""
        return "_".join((
            self._rng.choice(GROUP_TEMPERAMENT),
            self._rng.choice(GROUP_FORCE),
            self._rng.choice(GROUP_CREATURE),
        ))

    def generate_unique_name(
        self,
        is_taken: Callable[[str], bool],
        *,
        reroll_attempts: int = 8,
        suffix_attempts: int = 20,
    ) -> str:
        """
        Return a name for which `is_taken(name)` is False.

        1. Re-roll a fresh gamertag up to `reroll_attempts` times.
        2. Then keep the last base name and append `_<NN>` (01..99) up to
           `suffix_attempts` times.

        `is_taken` is the collision oracle. When driving real registration,
        pass an oracle whose 201 path captures the credentials (see `register`)
        so the very call that proves uniqueness is the one that registers.
        """
        last = self.generate_name()
        for _ in range(reroll_attempts):
            if not is_taken(last):
                return last
            last = self.generate_name()

        for _ in range(suffix_attempts):
            candidate = f"{last}_{self._rng.randint(1, 99):02d}"
            if not is_taken(candidate):
                return candidate

        raise ArenaNameExhausted(
            f"No free Arena name after {reroll_attempts} re-rolls + "
            f"{suffix_attempts} suffixed attempts (last base: {last!r})"
        )

    # ── Arena API ───────────────────────────────────────────────────────────

    def register(
        self,
        *,
        name: Optional[str] = None,
        description: str = "via NarraNexus",
    ) -> ArenaCredentials:
        """
        Register an Agent on Arena and return its credentials.

        If `name` is None, a random gamertag is generated and the register call
        itself is used as the uniqueness oracle (409 → re-roll, 201 → keep). If
        `name` is given, it is registered as-is (raises `ArenaApiError` on 409).
        """
        captured: Dict[str, object] = {}

        def is_taken(candidate: str) -> bool:
            body = {"name": candidate, "description": description}
            if self.referral_code:
                body["referralCode"] = self.referral_code
            resp = self._http().post(
                f"{self.api_base}/api/v1/agents/register",
                headers=self._headers,
                json=body,
            )
            if resp.status_code == 201:
                captured["data"] = resp.json()
                captured["name"] = candidate
                return False
            if resp.status_code == 409:
                return True
            raise ArenaApiError(
                f"register('{candidate}') -> HTTP {resp.status_code}: {resp.text[:300]}"
            )

        if name is None:
            self.generate_unique_name(is_taken)
        else:
            if is_taken(name):
                raise ArenaApiError(f"Arena name already taken: {name!r}")

        data = captured["data"]  # type: ignore[assignment]
        agent = data.get("agent") or {}  # type: ignore[union-attr]
        creds = data.get("credentials") or {}  # type: ignore[union-attr]
        claim_token = creds.get("claim_token")
        # claim_url is not always in the register body; derive it from the token
        # (Arena's claim page is /claim/<claim_token>).
        claim_url = data.get("claim_url")  # type: ignore[union-attr]
        if not claim_url and claim_token:
            claim_url = f"https://arena42.ai/claim/{claim_token}"
        return ArenaCredentials(
            api_key=creds.get("api_key"),
            agent_id=agent.get("id"),
            agent_name=captured["name"],  # type: ignore[arg-type]
            claim_token=claim_token,
            claim_url=claim_url,
            referral_code=data.get("referral_code"),  # type: ignore[union-attr]
        )

    def bind_owner(
        self, credentials: ArenaCredentials, user_token: str
    ) -> Dict[str, Optional[str]]:
        """
        Bind the owner email to this Arena agent via Arena's platform-only
        endpoint, using the user's NetMind JWT — no email-verification round-trip.

        Arena normally binds an owner email through a user-clicked verification
        link. Because NarraNexus provisions agents programmatically (no inbox to
        click), Arena exposes ``POST /api/v1/agents/me/platform-bind-owner``:
        authenticate as the agent (its api_key) and pass the user's NetMind JWT
        in the body; Arena verifies the JWT against the shared NetMind account
        system and writes ``agent.ownerEmail``.

          POST {api_base}/api/v1/agents/me/platform-bind-owner
            Authorization: Bearer <agent api_key>
            body: {"user_token": "<NetMind JWT, NO Bearer prefix>"}

        Best-effort by contract — owner email is optional, so this NEVER raises:
        a failure must not abort provisioning. Returns a structured status the
        caller records for observability / opportunistic retry:

          status ∈ {
            "bound"           — 200, email written (``email`` populated),
            "skipped_no_email"— 200, the NetMind account has no email on record,
            "already_bound"   — 400 EMAIL_ALREADY_BOUND (idempotent success),
            "invalid_token"   — 401 INVALID_TOKEN (the user_token is bad/expired),
            "rate_limited"    — 429,
            "error"           — anything else, missing api_key/token, or transport
          }

        Idempotent on Arena's side: a repeat call returns EMAIL_ALREADY_BOUND,
        which we map to ``already_bound`` (a terminal success the caller can use
        to skip future attempts).
        """
        def _result(status, *, email=None, http_status=None, detail=""):
            return {
                "status": status,
                "email": email,
                "http_status": http_status,
                "detail": detail,
            }

        api_key = credentials.api_key
        if not api_key:
            return _result("error", detail="no agent api_key to authenticate bind")
        if not user_token:
            return _result("error", detail="no user_token (NetMind JWT) provided")

        try:
            resp = self._http().post(
                f"{self.api_base}/api/v1/agents/me/platform-bind-owner",
                headers={**self._headers, "Authorization": f"Bearer {api_key}"},
                json={"user_token": user_token},
            )
        except httpx.HTTPError as exc:  # transport failure — never fatal here
            return _result("error", detail=f"transport error: {exc}")

        try:
            body = resp.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        code = str(body.get("code") or "").upper()
        message = str(body.get("message") or "")

        if resp.status_code == 200:
            email = body.get("email")
            if email:
                return _result("bound", email=email, http_status=200, detail=message)
            # 200 with no email → the NetMind account carries no email; Arena
            # returns 200 but performs no binding ("binding skipped").
            return _result("skipped_no_email", http_status=200, detail=message)
        if resp.status_code == 400 and code == "EMAIL_ALREADY_BOUND":
            return _result("already_bound", http_status=400, detail=message)
        if resp.status_code == 401:
            return _result("invalid_token", http_status=401, detail=message or code)
        if resp.status_code == 429:
            return _result("rate_limited", http_status=429, detail=message)
        return _result(
            "error",
            http_status=resp.status_code,
            detail=f"unexpected response: {resp.text[:300]}",
        )

    def fetch_skill_md(self) -> str:
        """Fetch Arena's skill.md (the agent-facing instructions)."""
        resp = self._http().get(self.skill_md_url, headers=self._headers)
        resp.raise_for_status()
        return resp.text

    def verify_credentials(self, credentials: ArenaCredentials) -> Dict:
        """GET /agents/me with the key — proves the registration is live."""
        resp = self._http().get(
            f"{self.api_base}/api/v1/agents/me",
            headers={**self._headers, "Authorization": f"Bearer {credentials.api_key}"},
        )
        resp.raise_for_status()
        return resp.json()

    # ── Workspace skill installation ────────────────────────────────────────

    def install_skill(
        self,
        skills_dir: Path | str,
        credentials: ArenaCredentials,
        *,
        skill_md: Optional[str] = None,
        skill_name: str = DEFAULT_SKILL_NAME,
        owner_user_id: Optional[str] = None,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> ArenaOnboardResult:
        """
        Write the skill into `<skills_dir>/<skill_name>/`:
          - SKILL.md            (Arena's instructions; fetched if not supplied)
          - .skill_meta.json    (SkillModule format; env values base64-encoded)
          - credentials.json    (secret: api_key + claim_token; chmod 0600)
          - arena_profile.json  (non-secret provenance + config)

        Returns the result with the skill dir and the files written.
        """
        skills_dir = Path(skills_dir)
        skill_dir = skills_dir / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)

        if skill_md is None:
            skill_md = self.fetch_skill_md()

        registered_at = datetime.now(timezone.utc).isoformat()

        env_plain: Dict[str, str] = {
            "ARENA_API_KEY": credentials.api_key or "",
            "ARENA_AGENT_ID": credentials.agent_id or "",
            # SKILL.md reads the API base from `ARENA_API_URL` (highest
            # priority); match that exact name, not a near-miss like
            # ARENA_API_BASE, or the skill falls back to its hard-coded default.
            "ARENA_API_URL": self.api_base,
            "ARENA_SKILL_VERSION": self.skill_version,
        }
        if extra_env:
            env_plain.update(extra_env)
        env_config = {
            k: base64.b64encode(v.encode("utf-8")).decode("utf-8")
            for k, v in env_plain.items()
            if v
        }

        meta = {
            "source_url": self.skill_md_url,
            "source_type": "skill_md_url",
            "installed_at": registered_at,
            "requires": {"env": sorted(env_plain.keys()), "bins": []},
            "env_config": env_config,
        }

        profile = {
            "arena_agent_id": credentials.agent_id,
            "agent_name": credentials.agent_name,
            "referral_code": credentials.referral_code,
            "claim_url": credentials.claim_url,
            "registered_at": registered_at,
            "api_base": self.api_base,
            "skill_version": self.skill_version,
            "owner_user_id": owner_user_id,
            "provisioned_by": "NarraNexus",
        }

        files: List[str] = []
        # Always-latest Arena content (fetched fresh above) + our regenerated
        # override header from the single-source directive (keyed to this agent).
        header = skill_md_override_header(credentials.agent_name or "this agent")
        (skill_dir / "SKILL.md").write_text(header + skill_md, encoding="utf-8")
        files.append("SKILL.md")
        (skill_dir / ".skill_meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        files.append(".skill_meta.json")
        cred_file = skill_dir / "credentials.json"
        cred_file.write_text(
            json.dumps(credentials.as_credentials_json(), indent=2), encoding="utf-8"
        )
        cred_file.chmod(0o600)  # secret: api_key + claim_token
        files.append("credentials.json")
        (skill_dir / "arena_profile.json").write_text(
            json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        files.append("arena_profile.json")

        return ArenaOnboardResult(
            credentials=credentials, skill_dir=skill_dir, files_written=files
        )

    # ── One-shot ────────────────────────────────────────────────────────────

    def onboard(
        self,
        workspace_path: Path | str,
        *,
        name: Optional[str] = None,
        description: str = "via NarraNexus",
        skill_name: str = DEFAULT_SKILL_NAME,
        owner_user_id: Optional[str] = None,
        verify: bool = False,
    ) -> ArenaOnboardResult:
        """
        Register on Arena and install the skill under
        `<workspace_path>/skills/<skill_name>/` in one call.

        `workspace_path` is the agent's workspace root (e.g.
        `{base_working_path}/{agent_id}_{user_id}`). Pass `verify=True` to
        round-trip the new key through GET /agents/me before returning.
        """
        credentials = self.register(name=name, description=description)
        if verify:
            self.verify_credentials(credentials)
        skills_dir = Path(workspace_path) / "skills"
        return self.install_skill(
            skills_dir, credentials, skill_name=skill_name, owner_user_id=owner_user_id
        )
