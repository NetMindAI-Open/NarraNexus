"""
@file_name: routes.py
@author: NetMind.AI
@date: 2025-11-28
@description: Agent Social Network routes

Read endpoints:
- GET /{agent_id}/social-network - Get all social entities for an Agent
- GET /{agent_id}/social-network/{user_id} - Get social network info for a specific user
- GET /{agent_id}/social-network/search - Search social entities (keyword/semantic)

Write endpoints (2026-08-10, PR-2 · MCP data-access seam backend half):
- POST /{agent_id}/social-network/extract - extract/update entity info
- POST /{agent_id}/social-network/merge - merge two entity records
- POST /{agent_id}/social-network/delete-entity - permanently delete an entity
- POST /{agent_id}/social-network/create-agent - create a new agent owned by the caller

Every write endpoint mirrors the corresponding tool in
`narranexus/platform/module_system/social_network_module/_social_mcp_tools.py`
(same repository/module calls, same data semantics) so an HTTP caller and an
agent's own MCP tool call produce identical results — this route is the
non-agent-triggered path to the same data.
"""

import json
from typing import Any

from fastapi import APIRouter, Query, Request
from loguru import logger
from pydantic import BaseModel, Field

from narranexus.platform.utils.db.db_factory import get_db_client
from narranexus.platform.utils import format_for_api
from narranexus.platform.repository import (
    SocialNetworkRepository,
    InstanceRepository,
    AgentRepository,
)
from narranexus.platform.bootstrap.provision import provision_new_agent
from narranexus_plugins.social_network_module import (
    SocialNetworkModule,
    social_instance_not_found_msg,
    format_contact_result,
    format_stats_result,
    format_create_agent_success,
    CREATE_AGENT_NO_OWNER_MSG,
    create_agent_text_reject,
    default_created_by_description,
)
from narranexus.platform.schema import (
    StrippedText,
    normalize_agent_text,
    SocialNetworkEntityInfo,
    SocialNetworkResponse,
    SocialNetworkListResponse,
    SocialNetworkSearchResponse,
)

# Ownership gate (backend/routes/_ownership.py): agent_id is attacker-
# controlled path input — without the owner check any caller could extract/
# merge/delete another user's social-network entities, or spin up agents
# under someone else's account. Local mode (no JWT identity) does not
# enforce; see the helper's security-posture docstring before assuming auth.
from narranexus.sdk.web import require_agent_owner


router = APIRouter()


def _parse_json(value: Any, default: Any) -> Any:
    """
    Parse JSON field

    Handles JSON strings stored in the database, converting them to Python objects.
    Supports dict and list JSON types, also handles double-encoding cases.
    """
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            # Handle double-encoding
            if isinstance(parsed, str):
                try:
                    parsed = json.loads(parsed)
                except json.JSONDecodeError:
                    pass
            return parsed
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse failed for value: {value[:100]}... Error: {e}")
            return default
    logger.warning(f"Unexpected type for JSON field: {type(value)}, value: {value}")
    return default


@router.get("/{agent_id}/social-network/search", response_model=SocialNetworkSearchResponse)
async def search_social_network_entities(
    agent_id: str,
    request: Request,
    query: str = Query(..., description="Search query"),
    search_type: str = Query("semantic", description="Search type: 'keyword' or 'semantic'"),
    limit: int = Query(10, description="Maximum number of results")
):
    """
    Search social entities by keyword (entity_name, entity_description, tags).
    Owner-only (cloud mode). `search_type` is accepted for API compatibility
    but always resolves to keyword search — embedding/semantic search is retired.

    NOTE: This route MUST be registered before /{user_id} to avoid path shadowing.
    """
    await require_agent_owner(request, agent_id)
    logger.info(f"Searching social network entities: agent={agent_id}, query='{query}', type={search_type}")

    try:
        db_client = await get_db_client()

        instance_repo = InstanceRepository(db_client)
        instances = await instance_repo.get_by_agent(
            agent_id=agent_id,
            module_class="SocialNetworkModule"
        )

        if not instances:
            return SocialNetworkSearchResponse(
                success=True, entities=[], count=0, search_type=search_type
            )

        instance_id = instances[0].instance_id
        social_repo = SocialNetworkRepository(db_client)

        # Embeddings retired — entity search is BM25-style keyword search only.
        results = await social_repo.keyword_search(
            instance_id=instance_id,
            keyword=query,
            limit=limit
        )
        entity_list = []
        for entity in results:
            entity_info = SocialNetworkEntityInfo(
                entity_id=entity.entity_id,
                entity_name=entity.entity_name,
                aliases=entity.aliases or [],
                entity_description=entity.entity_description,
                entity_type=entity.entity_type,
                familiarity=entity.familiarity or "known_of",
                identity_info=entity.identity_info or {},
                contact_info=entity.contact_info or {},
                tags=entity.keywords or [],
                keywords=entity.keywords or [],
                relationship_strength=entity.relationship_strength or 0.0,
                interaction_count=entity.interaction_count or 0,
                last_interaction_time=format_for_api(entity.last_interaction_time),
                persona=entity.persona,
                related_job_ids=entity.related_job_ids or [],
                expertise_domains=entity.expertise_domains or [],
            )
            entity_list.append(entity_info)

        return SocialNetworkSearchResponse(
            success=True,
            entities=entity_list,
            count=len(entity_list),
            search_type=search_type
        )

    except Exception as e:
        logger.exception(f"Error searching social network entities: {e}")
        return SocialNetworkSearchResponse(
            success=False, error="Failed to search social network.", search_type=search_type
        )


def _entity_to_info(e) -> SocialNetworkEntityInfo:
    """Map a SocialNetworkEntity (from the unified-memory-backed repo) to the
    API's SocialNetworkEntityInfo. `tags` and `keywords` are the same list."""
    return SocialNetworkEntityInfo(
        entity_id=e.entity_id,
        entity_name=e.entity_name,
        aliases=e.aliases,
        entity_description=e.entity_description,
        entity_type=e.entity_type,
        familiarity=e.familiarity or "known_of",
        identity_info=e.identity_info,
        contact_info=e.contact_info,
        tags=e.keywords,
        keywords=e.keywords,
        relationship_strength=e.relationship_strength,
        interaction_count=e.interaction_count,
        last_interaction_time=format_for_api(e.last_interaction_time),
        persona=e.persona,
        related_job_ids=e.related_job_ids,
        expertise_domains=e.expertise_domains,
    )


@router.get("/{agent_id}/social-network/{user_id}", response_model=SocialNetworkResponse)
async def get_user_social_network_info(agent_id: str, user_id: str, request: Request):
    """
    Get a user's information in the Agent's social network

    Owner-only (cloud mode). Queries data from instance_social_entities table
    (via SocialNetworkModule's instance_id).
    """
    await require_agent_owner(request, agent_id)
    logger.info(f"Getting social network info for user: {user_id}, agent: {agent_id}")

    try:
        db_client = await get_db_client()

        instance_repo = InstanceRepository(db_client)
        instances = await instance_repo.get_by_agent(
            agent_id=agent_id,
            module_class="SocialNetworkModule"
        )

        if not instances:
            return SocialNetworkResponse(
                success=False,
                error=social_instance_not_found_msg(agent_id)
            )

        instance_id = instances[0].instance_id

        entity = await SocialNetworkRepository(db_client).get_entity(user_id, instance_id)
        if entity:
            return SocialNetworkResponse(success=True, entity=_entity_to_info(entity))
        else:
            return SocialNetworkResponse(
                success=False,
                error=f"No social network info found for user: {user_id}"
            )

    except Exception as e:
        logger.exception(f"Error getting social network info: {e}")
        return SocialNetworkResponse(success=False, error="Failed to get social network info.")


@router.get("/{agent_id}/social-network", response_model=SocialNetworkListResponse)
async def get_all_social_network_entities(agent_id: str, request: Request):
    """
    Get all social entities for an Agent

    Owner-only (cloud mode). Queries data from instance_social_entities table
    (via SocialNetworkModule's instance_id).
    """
    await require_agent_owner(request, agent_id)
    logger.debug(f"Getting all social network entities for agent: {agent_id}")

    try:
        db_client = await get_db_client()

        instance_repo = InstanceRepository(db_client)
        instances = await instance_repo.get_by_agent(
            agent_id=agent_id,
            module_class="SocialNetworkModule"
        )

        if not instances:
            return SocialNetworkListResponse(success=True, entities=[], count=0)

        instance_id = instances[0].instance_id

        entities = await SocialNetworkRepository(db_client).get_all_entities(instance_id, limit=1000)
        entity_list = [_entity_to_info(e) for e in entities]

        return SocialNetworkListResponse(
            success=True,
            entities=entity_list,
            count=len(entity_list)
        )

    except Exception as e:
        logger.exception(f"Error getting social network entities: {e}")
        return SocialNetworkListResponse(success=False, error="Failed to get social network entities.")


# ============================================================================= Write endpoints


class ExtractEntityBody(BaseModel):
    """Body for POST .../extract — mirrors the `extract_entity_info` MCP
    tool's params minus `agent_id` (taken from the path instead)."""
    entity_id: str = Field(min_length=1, max_length=128)
    updates: dict[str, Any]
    update_mode: str = "merge"


class MergeEntitiesBody(BaseModel):
    """Body for POST .../merge — mirrors the `merge_entities` MCP tool's params."""
    source_entity_id: str = Field(min_length=1, max_length=128)
    target_entity_id: str = Field(min_length=1, max_length=128)
    keep_target_name: bool = True


class DeleteEntityBody(BaseModel):
    """Body for POST .../delete-entity — mirrors the `delete_entity` MCP tool's params."""
    entity_id: str = Field(min_length=1, max_length=128)


class CreateAgentBody(BaseModel):
    """Body for POST .../create-agent — mirrors the `create_agent` MCP tool's
    params minus `agent_id` (the creator, taken from the path instead).

    `new_agent_id` is MINTED BY THE CALLER (the MCP tool) and passed in, so the
    seam's DirectStore and this route provision the SAME id and return
    byte-identical output — the route no longer generates its own. It is
    CONSTRAINED to the tool's minted form `agent_<12 hex>` by the pattern below:
    the id becomes a filesystem path segment (agent_workspace_path builds
    base/{user_id}/{agent_id}), so an unconstrained value like
    "../other_user/agent" would traverse into another tenant's workspace — the
    pattern makes that impossible, and provision_new_agent re-checks it as a
    defense-in-depth backstop for every call site."""
    new_agent_id: str = Field(min_length=1, max_length=64, pattern=r"^agent_[0-9a-f]{12}$")
    # No `min_length`: the route itself refuses an empty name with the
    # SHARED message its DirectStore twin uses. A 422 here instead would
    # hand the model a transport-level failure string on the HTTP path and
    # the shared constant on the local one — for the same tool call. That
    # split is exactly what the shared constant exists to prevent.
    # `StrippedText` so the stored form is what any later check measures, but
    # NO `AGENT_TEXT_MAX_LENGTH` here: a model-level cap answers 422 before the
    # handler runs, and this route's only caller is the HttpStore leg of the
    # `create_agent` tool — so a 422 hands the model "invalid arguments…" while
    # the DirectStore leg answers with a message naming the limit. Same tool
    # call, two answers, and only one of them tells the model how to fix it.
    # The real ceiling is enforced below by both legs through
    # CREATE_AGENT_TEXT_TOO_LONG_MSG. What is left here is a DoS backstop only,
    # deliberately far above any legitimate value (same order as `awareness`).
    agent_name: StrippedText = Field(max_length=65536)
    # NOT part of that rule — awareness is not an `agents` column.
    awareness: str = Field(default="", max_length=65536)
    agent_description: StrippedText = Field(default="", max_length=65536)


# ============================================================================= Read (seam-twin) endpoints
# These are the byte-parity HTTP twins of the READ MCP tools (search /
# get_contact_info / get_agent_social_stats). They are POST — not GET — so their
# action sub-paths (/recall, /contact, /stats) can't collide with the existing
# GET /{agent_id}/social-network/{user_id} path parameter, and they return the
# tool's dict shape verbatim (message-keyed failures, no _normalize_write_result)
# so the HttpStore path passes the body straight through. Owner-gated like the
# write twins.


class RecallSocialBody(BaseModel):
    """Body for POST .../recall — mirrors `search_social_network` tool params."""
    search_keyword: str = Field(min_length=1, max_length=512)
    search_type: str = "auto"
    top_k: int = Field(default=5, ge=1, le=100)


class ContactInfoBody(BaseModel):
    """Body for POST .../contact — mirrors `get_contact_info` tool params."""
    entity_id: str = Field(min_length=1, max_length=128)


class AgentStatsBody(BaseModel):
    """Body for POST .../stats — mirrors `get_agent_social_stats` tool params.
    filter_tags is already the parsed list (the tool splits the comma string)."""
    sort_by: str = "recent"
    top_k: int = Field(default=5, ge=1, le=100)
    filter_tags: list[str] | None = None


@router.post("/{agent_id}/social-network/recall")
async def recall_social_network(agent_id: str, body: RecallSocialBody, request: Request) -> dict:
    """Search the social network — byte-parity twin of the `search_social_network`
    MCP tool: same `SocialNetworkModule.search_network` call, same raw dict.
    The whole body is wrapped so an instance-resolution db failure answers 200
    with the tool's message shape (matching DirectStore), never a 500 — the
    store docstring's 'handlers answer 200' contract."""
    await require_agent_owner(request, agent_id)
    try:
        db_client = await get_db_client()
        instance_id, error = await _resolve_social_instance_id(db_client, agent_id)
        if error:
            return {"success": False, "message": error, "results": []}
        temp_module = SocialNetworkModule(agent_id=agent_id, database_client=db_client, instance_id=instance_id)
        return await temp_module.search_network(
            search_keyword=body.search_keyword, instance_id=instance_id,
            search_type=body.search_type, top_k=body.top_k,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Error searching social network for {agent_id}: {e}")
        return {"success": False, "message": f"Error: {e}", "results": []}


@router.post("/{agent_id}/social-network/contact")
async def contact_info(agent_id: str, body: ContactInfoBody, request: Request) -> dict:
    """Contact details for one entity — byte-parity twin of `get_contact_info`.
    Shapes `recall_entity_info` via the shared `format_contact_result`."""
    await require_agent_owner(request, agent_id)
    try:
        db_client = await get_db_client()
        instance_id, error = await _resolve_social_instance_id(db_client, agent_id)
        if error:
            return {"success": False, "message": error}
        temp_module = SocialNetworkModule(agent_id=agent_id, database_client=db_client, instance_id=instance_id)
        recall = await temp_module.recall_entity_info(body.entity_id, instance_id)
        return format_contact_result(body.entity_id, recall)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Error reading contact info for {agent_id}: {e}")
        return {"success": False, "message": f"Error: {e}"}


@router.post("/{agent_id}/social-network/stats")
async def agent_social_stats(agent_id: str, body: AgentStatsBody, request: Request) -> dict:
    """Owner-perspective social stats — byte-parity twin of `get_agent_social_stats`.
    Shapes `get_agent_stats` via the shared `format_stats_result`."""
    await require_agent_owner(request, agent_id)
    try:
        db_client = await get_db_client()
        instance_id, error = await _resolve_social_instance_id(db_client, agent_id)
        if error:
            return {"success": False, "message": error, "results": []}
        temp_module = SocialNetworkModule(agent_id=agent_id, database_client=db_client, instance_id=instance_id)
        stats = await temp_module.get_agent_stats(
            instance_id=instance_id, sort_by=body.sort_by, top_k=body.top_k, filter_tags=body.filter_tags,
        )
        return format_stats_result(body.sort_by, stats)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Error reading social stats for {agent_id}: {e}")
        return {"success": False, "message": f"Error: {e}", "results": []}


async def _resolve_social_instance_id(db_client, agent_id: str) -> tuple[str | None, str | None]:
    """Resolve the agent's SocialNetworkModule instance id.

    Same repository call as the instance-lookup half of
    `_get_instance_and_module` in `_social_mcp_tools.py`. The "no instance"
    error text now comes from the shared `social_instance_not_found_msg` so the
    AgentDataStore seam's DirectStore and this route (the HttpStore path) return
    byte-identical text — the write tools migrated onto the seam depend on that.
    Its wording matches this file's sibling GET endpoints ("... for agent: X").
    """
    instance_repo = InstanceRepository(db_client)
    instances = await instance_repo.get_by_agent(agent_id=agent_id, module_class="SocialNetworkModule")
    if not instances:
        return None, social_instance_not_found_msg(agent_id)
    return instances[0].instance_id, None


def _normalize_write_result(result: dict) -> dict:
    """Map the wrapped module/tool 'message' failure key onto this route
    family's 'error' key (the shape the GET endpoints above use), leaving
    success payloads untouched. The MCP tools this file wraps always report
    failure via 'message', not 'error'."""
    if isinstance(result, dict) and result.get("success") is False and "error" not in result and "message" in result:
        result = dict(result)
        result["error"] = result.pop("message")
    return result


@router.post("/{agent_id}/social-network/extract")
async def extract_entity_info(agent_id: str, body: ExtractEntityBody, request: Request) -> dict:
    """
    Extract/update entity info — same operation as the `extract_entity_info`
    MCP tool, reached over HTTP instead of an agent tool call. Delegates to
    `SocialNetworkModule.extract_and_update_entity_info` so merge/replace
    semantics (tag dedup capped at 10, identity/contact deep-merge,
    entity_description protected from direct overwrite) stay identical to
    the agent-facing path.
    """
    await require_agent_owner(request, agent_id)

    try:
        db_client = await get_db_client()
        instance_id, error = await _resolve_social_instance_id(db_client, agent_id)
        if error:
            return {"success": False, "error": error}

        temp_module = SocialNetworkModule(agent_id=agent_id, database_client=db_client, instance_id=instance_id)
        result = await temp_module.extract_and_update_entity_info(
            entity_id=body.entity_id,
            instance_id=instance_id,
            updates=dict(body.updates),
            update_mode=body.update_mode,
        )
        return _normalize_write_result(result)

    except Exception as e:
        logger.exception(f"Error extracting entity info: {e}")
        return {"success": False, "error": "Failed to extract entity info."}


@router.post("/{agent_id}/social-network/merge")
async def merge_entities(agent_id: str, body: MergeEntitiesBody, request: Request) -> dict:
    """
    Merge two entity records — same operation as the `merge_entities` MCP
    tool: source is folded into target (tags/identity/contact_info/
    related_job_ids unioned, descriptions appended, interaction counts
    summed, newest last_interaction_time kept) then the source is deleted.

    Delegates to `SocialNetworkModule.merge_entities` (pre-open review #2:
    this used to be a byte-for-byte copy of the MCP tool's inline closure
    body; both the tool and this route now call the same module method so
    the merge semantics can't drift between the two entry points).
    """
    await require_agent_owner(request, agent_id)

    try:
        db_client = await get_db_client()
        instance_id, error = await _resolve_social_instance_id(db_client, agent_id)
        if error:
            return {"success": False, "error": error}

        temp_module = SocialNetworkModule(agent_id=agent_id, database_client=db_client, instance_id=instance_id)
        result = await temp_module.merge_entities(
            source_entity_id=body.source_entity_id,
            target_entity_id=body.target_entity_id,
            instance_id=instance_id,
            keep_target_name=body.keep_target_name,
        )
        return _normalize_write_result(result)

    except Exception as e:
        logger.exception(f"Error merging entities: {e}")
        return {"success": False, "error": "Failed to merge entities."}


@router.post("/{agent_id}/social-network/delete-entity")
async def delete_entity(agent_id: str, body: DeleteEntityBody, request: Request) -> dict:
    """
    Permanently delete a social network entity — same operation as the
    `delete_entity` MCP tool. POST (not HTTP DELETE) so the target entity_id
    travels in the body, consistent with this route family's other write
    endpoints.

    Delegates to `SocialNetworkModule.delete_entity` (pre-open review #2 —
    see the `merge` endpoint above for the same rationale).
    """
    await require_agent_owner(request, agent_id)

    try:
        db_client = await get_db_client()
        instance_id, error = await _resolve_social_instance_id(db_client, agent_id)
        if error:
            return {"success": False, "error": error}

        temp_module = SocialNetworkModule(agent_id=agent_id, database_client=db_client, instance_id=instance_id)
        result = await temp_module.delete_entity(entity_id=body.entity_id, instance_id=instance_id)
        return _normalize_write_result(result)

    except Exception as e:
        logger.exception(f"Error deleting entity: {e}")
        return {"success": False, "error": "Failed to delete entity."}


@router.post("/{agent_id}/social-network/create-agent")
async def create_agent(agent_id: str, body: CreateAgentBody, request: Request) -> dict:
    """
    Create a new agent owned by the caller — same operation as the
    `create_agent` MCP tool, via the shared `provision_new_agent` seam: agent
    row + default module instances + peer-discovery registration + bootstrap
    profile + default-skill install + awareness seed. `agent_id` in the path
    is the CREATOR (must be owned by the caller); the new agent inherits the
    creator's `created_by`, i.e. it lands in the same user's account, not the
    creator agent's. Any non-fatal provisioning warning (a half-provisioned
    agent) is surfaced in the response so ops can see it (incident lesson #5).
    """
    await require_agent_owner(request, agent_id)

    try:
        db_client = await get_db_client()
        agent_repo = AgentRepository(db_client)
        caller = await agent_repo.get_agent(agent_id)
        if not caller or not caller.created_by:
            return {"success": False, "error": CREATE_AGENT_NO_OWNER_MSG}

        owner_user_id = caller.created_by
        new_agent_id = body.new_agent_id  # minted by the caller (parity — see body doc)

        # Normalized before the checks below, exactly as the DirectStore twin
        # does it: the row is stored normalized (AgentRepository.add_agent), so
        # an unnormalized name here would make the success echo disagree with
        # what was written, and the `or` fallback would be skipped by a
        # whitespace-only description.
        agent_name = normalize_agent_text(body.agent_name)
        agent_description = normalize_agent_text(body.agent_description)
        # The rule (which checks, in which order, answering with which sentence)
        # lives in the shared seam, so this leg and DirectStore cannot drift —
        # sharing only the message constants is what let them drift before.
        refusal = create_agent_text_reject(agent_name, agent_description)
        if refusal:
            return {"success": False, "error": refusal}

        # Canonical provisioning seam (pre-open review #3): agent row +
        # default module instances + peer-discovery registration + bootstrap
        # profile + default-skill install + awareness seed, all in one call.
        # Both this route and the MCP `create_agent` tool go through the same
        # `provision_new_agent` (via the AgentDataStore seam's DirectStore) and
        # the shared `format_create_agent_success`, so they can't drift.
        result = await provision_new_agent(
            db_client,
            agent_id=new_agent_id,
            user_id=owner_user_id,
            agent_name=agent_name,
            agent_description=agent_description
            or default_created_by_description(caller.agent_name or agent_id),
            awareness=body.awareness,
        )
        logger.info(f"Created agent {new_agent_id} ('{agent_name}') for owner {owner_user_id}")
        return format_create_agent_success(agent_name, new_agent_id, result.warnings)

    except Exception as e:
        logger.exception(f"Error creating agent: {e}")
        return {"success": False, "error": f"Error: {e}"}


# ---- plugin contribution (batch 3c.4): this twin router of the data-access
# seam is provided by builtin.social_network through backend.routes (mounted under /api/agents
# by backend.plugins_host, no longer included by routes/agents/core.py), so the
# HTTP twin disappears together with the capability when the plugin is disabled.
from narranexus.contracts.route import RouterSpec  # noqa: E402
from narranexus.kernel.plugins.registry import Contribution  # noqa: E402

ROUTES = (Contribution("agents_social_network", lambda: RouterSpec(router, "/api/agents", tags=("Agents",))),)
