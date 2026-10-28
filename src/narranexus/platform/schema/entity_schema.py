"""
@file_name: entity_schema.py
@author: NetMind.AI
@date: 2025-12-02
@description: Entity data model Schema

Centralized management of all entity data models, for use by the Repository layer
and other modules

Includes:
- SocialNetworkEntity: Social network entity
- User: User entity
- UserStatus: User status enum
- Agent: Agent entity
- MCPUrl: MCP URL entity
"""

from datetime import datetime
from enum import Enum
from typing import Annotated, List, Dict, Any, Optional
from pydantic import BaseModel, BeforeValidator, Field


# ===== User Status Enum =====

class UserStatus(str, Enum):
    """User status enum"""
    ACTIVE = "active"
    INACTIVE = "inactive"
    BLOCKED = "blocked"
    DELETED = "deleted"
    # A distinct, administratively-set account state. Kept separate from
    # BLOCKED/DELETED so the account-suspension mechanism has its own value
    # (a reinstate returns the row to ACTIVE, and an existing "banned" row in
    # the DB stays loadable rather than raising on enum coercion).
    BANNED = "banned"


# The account states that must not transact. A single shared source of truth
# for every surface that gates on account state (the HTTP auth middleware, the
# WebSocket run gate, and the netmind-login gate) so the set can never drift
# between them. Purely a set of ``users.status`` values — this constant holds no
# policy about how an account reaches one of them. INACTIVE is deliberately NOT
# here: it is a benign lifecycle state (never logged in / dormant), not a
# suspension. ``banned`` is what the suspension mechanism sets; ``blocked`` /
# ``deleted`` are pre-existing terminal states that equally must not transact.
NON_TRANSACTING_USER_STATUSES: frozenset[str] = frozenset(
    {
        UserStatus.BANNED.value,
        UserStatus.BLOCKED.value,
        UserStatus.DELETED.value,
    }
)


# ===== Social Network Entity =====

#: Column width of `social_network_entities.entity_name` on MySQL
#: (VARCHAR(255); TEXT on SQLite). Writers that persist a caller-controlled
#: name truncate to this so an over-long value cannot fail the write with a
#: 1406 on MySQL. Mirrors the DDL in `utils/db/schema_registry.py`.
ENTITY_NAME_MAX_LEN = 255


class SocialNetworkEntity(BaseModel):
    """
    Social Network Entity data model

    Records information about entities (users or other Agents) in the Instance's social network

    Refactoring notes (2025-12-24):
    - owner_agent_id changed to instance_id
    - Data follows the Instance, rather than being directly tied to agent_id
    """
    # Database auto-increment ID
    id: Optional[int] = None

    # Instance association (core refactoring point)
    instance_id: str = Field(..., max_length=64, description="Associated SocialNetworkModule Instance ID")

    # Entity identifier (required)
    entity_id: str = Field(..., max_length=64, description="Entity ID (user_id or agent_id)")
    entity_type: str = Field(..., max_length=32, description="Entity type: user | agent | group")

    # Entity basic information
    entity_name: Optional[str] = Field(None, max_length=255, description="Entity name/nickname")
    aliases: List[str] = Field(
        default=[],
        description="Cross-system identifiers and alternate names (e.g., Lark open_ids, platform agent IDs)"
    )
    entity_description: Optional[str] = Field(None, description="Entity brief description")

    # Core field: Identity information (JSON format)
    identity_info: Dict[str, Any] = Field(
        default={},
        description="Identity info JSON: organization, position, expertise, preferences, etc."
    )

    # Contact information (JSON format)
    contact_info: Dict[str, Any] = Field(
        default={},
        description="Contact info JSON: chat_channel, email, preferred_method, etc."
    )

    # Familiarity level (cognitive tier)
    familiarity: str = Field(
        default="known_of",
        max_length=32,
        description="Familiarity level: direct (interacted with) | known_of (mentioned by others)"
    )

    # Relationship metadata
    relationship_strength: float = Field(
        default=0.0,
        description="Relationship strength 0.0-1.0"
    )
    interaction_count: int = Field(
        default=0,
        description="Interaction count"
    )
    last_interaction_time: Optional[datetime] = Field(
        None,
        description="Last interaction time"
    )

    # Keyword system (for search and classification)
    # NOTE: DB column is still named 'tags' — mapping handled in repository layer
    keywords: List[str] = Field(
        default=[],
        description="Keyword list: ['bitcoin_forum', 'expert:recommendation_system', 'engineer']"
    )

    # Expertise domains (for intelligent matching and recommendations)
    expertise_domains: List[str] = Field(
        default=[],
        description="Expertise domain list JSON: ['recommendation_system', 'machine_learning', 'deep_learning']"
    )

    # === Job association (Feature 2.2.1 - bidirectional index) ===
    related_job_ids: List[str] = Field(
        default=[],
        description="List of associated Job IDs, for reverse lookup of all Jobs related to this Entity"
    )

    # 2026-05-27: the `embedding` field was removed together with the
    # semantic-search chain (Owner spec). The underlying DB column stays
    # in the schema_registry as a dormant column but the entity model no
    # longer round-trips it.

    # Persona (communication style guide)
    persona: Optional[str] = Field(
        default=None,
        description="Persona/style guide for communicating with this entity (natural language description)"
    )

    # Extra data (for extension fields)
    extra_data: Dict[str, Any] = Field(
        default={},
        description="Extra data JSON, for storing extension fields."
    )

    # Timestamps (managed automatically by database)
    created_at: Optional[datetime] = Field(default=None, description="Creation time")
    updated_at: Optional[datetime] = Field(default=None, description="Update time")


# ===== User Entity =====

class User(BaseModel):
    """User data model"""
    id: Optional[int] = None
    user_id: str = Field(..., max_length=64, description="Unique user identifier")
    user_type: str = Field(..., max_length=32, description="User type")
    display_name: Optional[str] = Field(None, max_length=255, description="Display name")
    email: Optional[str] = Field(None, max_length=255, description="Email")
    phone_number: Optional[str] = Field(None, max_length=32, description="Phone number")
    nickname: Optional[str] = Field(None, max_length=50, description="Nickname")
    timezone: str = Field(default="UTC", max_length=64, description="User timezone (IANA format, e.g., Asia/Shanghai)")
    status: UserStatus = Field(default=UserStatus.ACTIVE, description="User status")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Additional metadata")
    last_login_time: Optional[datetime] = Field(default=None, description="Last login time")
    create_time: Optional[datetime] = Field(default=None, description="Creation time")
    update_time: Optional[datetime] = Field(default=None, description="Update time")


# ===== Agent Entity =====

# Single source of truth for the agent_name / agent_description length ceiling.
# The `agents` DB column is VARCHAR(255) (MySQL) / TEXT (SQLite — no enforcement),
# so this cap is enforced at the application layer: the Agent model below (read
# path), the CreateAgent/UpdateAgent request models (write path), and the bundle
# importer (which trims to this length). Keep all three tied to this constant so
# the three limits can never drift apart again.
AGENT_TEXT_MAX_LENGTH = 255


def _strip_if_text(value: Any) -> Any:
    """Strip a supplied string; pass everything else through untouched.

    ``None`` must survive as ``None``: on an update path it is the only thing
    that distinguishes "field not supplied" from ``""`` ("clear this field").
    Non-str values are left for pydantic to reject with its own message.
    """
    return value.strip() if isinstance(value, str) else value


# A request-body string normalized before any other validation runs, so length
# caps measure what will actually be STORED (every writer of the agents row
# normalizes — see agent_field_matches). Without it, trailing whitespace pushes
# an otherwise-legal value over the cap on one write path while another, which
# measures after stripping, accepts the same input. Lives here rather than in
# api_schema because the manyfold request models need it too and api_schema
# cannot be imported from them without a cycle.
StrippedText = Annotated[str, BeforeValidator(_strip_if_text)]


class Agent(BaseModel):
    """Agent data model"""
    id: Optional[int] = None
    agent_id: str = Field(..., max_length=64, description="Unique Agent identifier")
    agent_name: str = Field(..., max_length=AGENT_TEXT_MAX_LENGTH, description="Agent name")
    created_by: str = Field(..., max_length=64, description="Creator")
    agent_description: Optional[str] = Field(
        None, max_length=AGENT_TEXT_MAX_LENGTH, description="Agent description"
    )
    agent_type: Optional[str] = Field(None, max_length=32, description="Agent type")
    is_public: bool = Field(default=False, description="Whether publicly visible (visible to all users)")
    agent_metadata: Optional[Dict[str, Any]] = Field(default=None, description="Additional metadata")
    agent_create_time: Optional[datetime] = Field(default=None, description="Creation time")
    agent_update_time: Optional[datetime] = Field(default=None, description="Update time")


# The string agent creation used to stamp into ``agent_description`` when the
# caller supplied none. Creation no longer writes it (an unset description is
# now empty), but ~488 production rows carry it, so every reader must still
# recognise it as "not set" rather than prose.
#
# It was never harmless filler: the bus registry snapshotted it, so
# ``bus_get_agent_profile`` reported a fully configured agent as "a new agent
# ready for configuration", and the ASKING agent concluded the peer was not
# ready and refused to send anything (P1 section 02, prod evt_feb1f6ae). BasicInfo
# injects the same field as the agent's own self-description, so the asked
# agent read it about itself too.
LEGACY_AGENT_DESCRIPTION_PLACEHOLDER = "A new agent ready for configuration"


def normalize_agent_text(value: Optional[str]) -> str:
    """The stored form of an agent's name / description.

    Surrounding whitespace is not content: a description saved with a trailing
    space is the same description, and ``build_discovery_description`` strips
    it again before peers ever see it. Normalising on the way IN keeps the
    stored row and every reader's view of it identical, instead of leaving the
    difference to whichever reader remembers to strip.

    ``None`` and ``""`` are both "no text" — the DB may hold either (rows
    created before the empty-string default still carry NULL), and a caller
    clearing a field sends ``""``.
    """
    return (value or "").strip()


# The agents-row columns whose value is free text: the ones that get
# normalized on write and compared as text. Deliberately a closed set — see the
# dispatch in agent_field_matches — and exported, because every writer of the
# row needs the same answer to "which columns is this rule about". Adding a
# text column here is what makes both the normalizer and the predicate cover
# it; two of them would drift the moment someone updated only one.
AGENT_TEXT_FIELDS = frozenset({"agent_name", "agent_description"})


def normalize_agent_row_text(values: dict) -> dict:
    """Return ``values`` with every agents-row text column normalized.

    A new dict; the input is left alone. Non-text keys pass through untouched,
    so a caller can hand this a whole row or a partial patch. ``is_public`` is
    deliberately NOT included: it is not text, and agent_field_matches
    dispatches it separately (bool on MySQL TINYINT vs SQLite INTEGER).

    Every writer of the agents row goes through this — `AgentRepository`, and
    the handful of paths that raw-insert. See the invariant note above
    :func:`agent_field_matches`.
    """
    return {
        k: normalize_agent_text(v) if k in AGENT_TEXT_FIELDS else v
        for k, v in values.items()
    }


def agent_field_matches(agent: "Agent", field: str, wanted: object) -> bool:
    """Does ``agent`` already hold ``wanted`` for ``field``?

    The single definition of "this write would change nothing", shared by
    every writer of the ``agents`` row. It lives here, beside the entity whose
    fields it compares, because the two existing writers reached opposite
    answers for the same input: the awareness tool compared stripped values
    while ``PUT /api/auth/agents`` compared raw ones, so one accepted a name
    the other treated as unchanged.

    The text fields are compared in their NORMALIZED form, so the predicate is
    only sound while the row holds normalized text. That is what lets a
    compare-then-verify writer trust "already equal": if a row could hold an
    unnormalized value, saving the stripped version of the "same" name would
    compare equal, issue no write, and then certify the untouched row.

    Keeping that true is a property of every writer, not of one chokepoint.
    :class:`AgentRepository` normalizes in ``add_agent`` / ``update_agent``,
    which covers the routes, the MCP tool, arena provisioning and the
    migration applier. The paths that raw-insert the table normalize at their
    own edge instead — today the manyfold agent routes and the bundle
    importer. Re-check the list before trusting it; it is one command:

        git grep -nE '(insert|update)\(\s*"agents"|_ins\("agents"' -- backend src

    Args:
        agent: the entity as currently stored.
        field: column name (``agent_name`` / ``agent_description`` /
            ``is_public`` / ``created_by``). Closed set — anything else raises,
            so adding one is a deliberate act with a comparison chosen for it.
        wanted: the value the caller asked for.

    Returns:
        True when no write is needed for this field.
    """
    if field == "is_public":
        # The column is TINYINT on MySQL and INTEGER on SQLite, and
        # ``_row_to_entity`` may hand back either a bool or an int.
        return bool(agent.is_public) == bool(wanted)
    if field == "created_by":
        if not isinstance(wanted, (str, type(None))):
            # The closed-set discipline this predicate is built on has no
            # exceptions: a non-str would compare unequal instead of raising,
            # forcing a write of a wrong-typed owner id. Same refusal the text
            # branch makes below, made here because this branch returns first.
            raise TypeError(
                f"agent_field_matches: created_by expects str, got "
                f"{type(wanted).__name__}"
            )
        # Owner id, compared byte-for-byte. Deliberately NOT run through
        # normalize_agent_text like the display-text fields: that helper strips
        # prose written for humans, and quietly reshaping an identifier used as
        # a lookup key is how a row ends up owned by a user id nothing else
        # resolves. An id either is the same id or it is not.
        return (agent.created_by or "") == (wanted or "")
    if field not in AGENT_TEXT_FIELDS:
        # Explicitly dispatched, never "whatever getattr returns": an
        # unlisted field would otherwise compare as text and — for the ones
        # defaulting to None — answer "already equal", which suppresses the
        # write and then certifies the unchanged row. Adding a field here is
        # a deliberate act, with a comparison chosen for it.
        raise ValueError(
            f"agent_field_matches: no comparison defined for {field!r}"
        )
    if not isinstance(wanted, str):
        # Same reasoning as the closed field set above, one level down: a
        # non-str coerced to "" would answer "already equal" for an empty
        # row — suppressing the write and then certifying it. The two
        # mistakes (wrong field name, wrong value type) fail identically and
        # are equally invisible, so they are refused identically.
        raise TypeError(
            f"agent_field_matches: {field!r} takes a str, got "
            f"{type(wanted).__name__}"
        )
    return normalize_agent_text(getattr(agent, field)) == normalize_agent_text(
        wanted
    )


def is_agent_description_unset(description: Optional[str]) -> bool:
    """True when an agent has no real self-description yet.

    Covers empty/None (agents created after the fix) and the legacy
    placeholder above, case- and padding-insensitively — a row written by a
    tool that trimmed or lower-cased it must not read as real prose.

    Callers must degrade by SAYING NOTHING about the description rather than
    printing it: repeating "a new agent ready for configuration" to a peer is
    worse than silence, because it asserts the peer is unconfigured.
    """
    text = (description or "").strip()
    if not text:
        return True
    return text.lower() == LEGACY_AGENT_DESCRIPTION_PLACEHOLDER.lower()


# ===== MCP URL Entity =====

class MCPUrl(BaseModel):
    """MCP URL data model"""
    id: Optional[int] = None
    mcp_id: str = Field(..., max_length=64, description="Unique MCP identifier")
    agent_id: str = Field(..., max_length=64, description="Unique Agent identifier")
    user_id: str = Field(..., max_length=64, description="Unique User identifier")
    name: str = Field(..., max_length=255, description="MCP name")
    url: str = Field(..., max_length=1024, description="MCP SSE URL")
    headers: Optional[Dict[str, str]] = Field(
        default=None,
        description="Custom HTTP headers sent on every request to this MCP (e.g. Authorization). Stored plaintext; must be masked in API responses.",
    )
    description: Optional[str] = Field(None, max_length=512, description="MCP description")
    is_enabled: bool = Field(default=True, description="Whether enabled")
    connection_status: Optional[str] = Field(None, max_length=32, description="Connection status")
    last_check_time: Optional[datetime] = Field(default=None, description="Last check time")
    last_error: Optional[str] = Field(None, max_length=1024, description="Last error message")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Additional metadata")
    created_at: Optional[datetime] = Field(default=None, description="Creation time")
    updated_at: Optional[datetime] = Field(default=None, description="Update time")
