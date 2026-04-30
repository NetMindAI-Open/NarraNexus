"""
@file_name: auth.py
@author: NetMind.AI
@date: 2025-11-28
@description: REST API routes for authentication and user management

Provides endpoints for:
- POST /api/auth/login - Login with user_id
- POST /api/auth/create-user - Create a new user (requires admin secret key)
- GET /api/auth/agents - Get all agents for a user
- POST /api/auth/agents - Create a new agent
- PUT /api/auth/agents/{agent_id} - Update agent info
- DELETE /api/auth/agents/{agent_id} - Cascade delete agent and all related data
"""

import os
from uuid import uuid4
from fastapi import APIRouter, Query, Request
from loguru import logger

from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.utils import format_for_api
from xyz_agent_context.repository import AgentRepository, UserRepository
from xyz_agent_context.schema import (
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
    AgentInfo,
    AgentListResponse,
    CreateAgentRequest,
    CreateAgentResponse,
    UpdateAgentRequest,
    UpdateAgentResponse,
    DeleteAgentResponse,
    CreateUserRequest,
    CreateUserResponse,
    UpdateTimezoneRequest,
    UpdateTimezoneResponse,
)
from backend.auth import (
    hash_password,
    verify_password,
    create_token,
    _is_cloud_mode,
    INVITE_CODE,
)
from xyz_agent_context.utils import is_valid_timezone
from xyz_agent_context.settings import settings as app_settings


router = APIRouter()


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """
    Login with user_id (+ password in cloud mode).

    - Local mode: user_id only, no password required
    - Cloud mode: user_id + password, returns JWT token
    """
    logger.info(f"Login attempt for user: {request.user_id}")

    try:
        db_client = await get_db_client()
        user_repo = UserRepository(db_client)

        user = await user_repo.get_user(request.user_id)

        if not user:
            logger.warning(f"User {request.user_id} not found")
            return LoginResponse(
                success=False,
                error="User not found. Please register first." if _is_cloud_mode()
                    else "User not found. Please contact administrator to create an account."
            )

        if _is_cloud_mode():
            # Cloud mode: verify password and return JWT
            if not request.password:
                return LoginResponse(success=False, error="Password is required")

            password_hash = user.password_hash if hasattr(user, 'password_hash') else None
            if not password_hash:
                # Legacy user without password — check raw DB row
                user_row = await db_client.get_one("users", {"user_id": request.user_id})
                password_hash = user_row.get("password_hash") if user_row else None

            if not password_hash:
                return LoginResponse(success=False, error="Account not set up for cloud login. Please register.")

            if not verify_password(request.password, password_hash):
                return LoginResponse(success=False, error="Invalid password")

            # Get role
            user_row = await db_client.get_one("users", {"user_id": request.user_id})
            role = (user_row.get("role") if user_row else None) or "user"

            await user_repo.update_last_login(request.user_id)
            token = create_token(request.user_id, role)
            logger.info(f"User {request.user_id} logged in (cloud, role={role})")
            return LoginResponse(
                success=True,
                user_id=request.user_id,
                token=token,
                role=role,
            )
        else:
            # Local mode: user_id only
            await user_repo.update_last_login(request.user_id)
            logger.info(f"User {request.user_id} logged in (local)")
            return LoginResponse(
                success=True,
                user_id=request.user_id,
            )

    except Exception as e:
        logger.exception(f"Error during login: {e}")
        return LoginResponse(success=False, error=str(e))


@router.post("/register", response_model=RegisterResponse)
async def register(request: RegisterRequest, http_request: Request):
    """
    Register a new user (cloud mode only). Requires invite code.
    """
    logger.info(f"Registration attempt for user: {request.user_id}")

    try:
        if not _is_cloud_mode():
            return RegisterResponse(success=False, error="Registration is only available in cloud mode")

        # Validate invite code. INVITE_CODE has no default — when the
        # operator hasn't set it, every comparison fails, registration
        # stays closed. Surface a clearer error so an admin can spot the
        # missing config quickly instead of debugging "invalid invite
        # code" reports from real users.
        if INVITE_CODE is None:
            logger.warning(
                "Registration attempted but server has no INVITE_CODE configured. "
                "Set the INVITE_CODE environment variable to enable registration."
            )
            return RegisterResponse(
                success=False,
                error="Registration is currently disabled on this server.",
            )
        if request.invite_code != INVITE_CODE:
            return RegisterResponse(success=False, error="Invalid invite code")

        # Validate password length
        if len(request.password) < 6:
            return RegisterResponse(success=False, error="Password must be at least 6 characters")

        # Validate user_id
        if len(request.user_id) < 2 or len(request.user_id) > 32:
            return RegisterResponse(success=False, error="Username must be 2-32 characters")

        db_client = await get_db_client()
        user_repo = UserRepository(db_client)

        # Check if user already exists
        existing = await user_repo.get_user(request.user_id)
        if existing:
            return RegisterResponse(success=False, error="Username already taken")

        # Create user with password
        password_hash = hash_password(request.password)
        await db_client.insert("users", {
            "user_id": request.user_id,
            "password_hash": password_hash,
            "role": "user",
            "user_type": "individual",
            "display_name": request.display_name or request.user_id,
            "status": "active",
        })

        # Generate token
        token = create_token(request.user_id, "user")
        logger.info(f"User {request.user_id} registered successfully")

        # Seed the system-default free-tier quota row for the new user.
        # Failures here must NOT fail the registration itself — the user
        # still gets their account, they just don't get the free tier
        # this run (staff can fix post-hoc via /api/admin/quota/init).
        quota_row = None
        quota_service = getattr(http_request.app.state, "quota_service", None)
        if quota_service is not None:
            try:
                quota_row = await quota_service.init_for_user(request.user_id)
            except Exception as e:
                logger.exception(
                    f"register: failed to init quota for {request.user_id}: {e}"
                )

        return RegisterResponse(
            success=True,
            user_id=request.user_id,
            token=token,
            has_system_quota=quota_row is not None,
            initial_input_tokens=(
                quota_row.initial_input_tokens if quota_row else 0
            ),
            initial_output_tokens=(
                quota_row.initial_output_tokens if quota_row else 0
            ),
        )

    except Exception as e:
        logger.exception(f"Error during registration: {e}")
        return RegisterResponse(success=False, error=str(e))


@router.get("/agents", response_model=AgentListResponse)
async def get_agents(
    user_id: str = Query(..., description="User ID (required), returns agents created by this user + public agents"),
):
    """
    Get the list of agents visible to the user

    Visibility rules:
    - Agents created by the user (created_by = user_id)
    - Agents set as public (is_public = 1)
    """
    logger.debug(f"Getting agents list for user: {user_id}")

    try:
        db_client = await get_db_client()

        query = """
            SELECT
                agent_id,
                agent_name,
                agent_description,
                agent_type,
                agent_create_time,
                created_by,
                is_public
            FROM agents
            WHERE created_by = %s OR is_public = 1
            ORDER BY agent_create_time DESC
        """
        rows = await db_client.execute(query, (user_id,))

        agents = []
        for row in rows:
            description = row.get('agent_description')
            # Check if Bootstrap.md exists for this agent (first-run setup pending)
            bootstrap_active = False
            created_by = row.get('created_by')
            if created_by:
                bootstrap_path = os.path.join(
                    app_settings.base_working_path,
                    f"{row['agent_id']}_{created_by}",
                    "Bootstrap.md"
                )
                bootstrap_active = os.path.isfile(bootstrap_path)
            agent_info = AgentInfo(
                agent_id=row['agent_id'],
                name=row.get('agent_name') or row['agent_id'],
                description=description[:200] + '...' if description and len(description) > 200 else description,
                status='active',
                created_at=format_for_api(row.get('agent_create_time')),
                is_public=bool(row.get('is_public', 0)),
                created_by=created_by,
                bootstrap_active=bootstrap_active,
            )
            agents.append(agent_info)

        logger.debug(f"Found {len(agents)} agents for user {user_id}")

        return AgentListResponse(
            success=True,
            agents=agents,
            count=len(agents),
        )

    except Exception as e:
        logger.exception(f"Error getting agents: {e}")
        return AgentListResponse(
            success=False,
            error=str(e)
        )


@router.post("/agents", response_model=CreateAgentResponse)
async def create_agent(request: CreateAgentRequest):
    """
    Create a new agent with default values
    Generates a unique agent_id automatically
    """
    logger.info(f"Creating new agent for user: {request.created_by}")

    try:
        db_client = await get_db_client()

        # Validate that the user exists
        user_repo = UserRepository(db_client)
        user = await user_repo.get_user(request.created_by)
        if not user:
            logger.warning(f"Cannot create agent: user {request.created_by} not found")
            return CreateAgentResponse(
                success=False,
                error="User not found. Please create an account first."
            )

        # Generate unique agent_id
        agent_id = f"agent_{uuid4().hex[:12]}"

        # Set default name if not provided
        agent_name = request.agent_name or "New Agent"
        agent_description = request.agent_description or "A new agent ready for configuration"

        # Add agent to database
        repo = AgentRepository(db_client)
        record_id = await repo.add_agent(
            agent_id=agent_id,
            agent_name=agent_name,
            created_by=request.created_by,
            agent_description=agent_description,
            agent_type="chat"
        )

        logger.info(f"Agent created: {agent_id}, record_id: {record_id}")

        # Compute workspace path (used by bootstrap)
        from xyz_agent_context.settings import settings
        workspace_path = os.path.join(
            settings.base_working_path,
            f"{agent_id}_{request.created_by}"
        )
        os.makedirs(workspace_path, exist_ok=True)

        # Eagerly create workspace and write Bootstrap.md for first-run setup
        try:
            from xyz_agent_context.bootstrap.template import BOOTSTRAP_MD_TEMPLATE

            bootstrap_file = os.path.join(workspace_path, "Bootstrap.md")
            with open(bootstrap_file, "w", encoding="utf-8") as f:
                f.write(BOOTSTRAP_MD_TEMPLATE)

            logger.info(f"Bootstrap.md written to {bootstrap_file}")
        except Exception as bootstrap_err:
            # Non-fatal: agent is already created, bootstrap is best-effort
            logger.warning(f"Failed to write Bootstrap.md: {bootstrap_err}")

        # Return the created agent info
        # Re-fetch from DB to get server-generated fields (created_at)
        agent_row = await db_client.get_one("agents", {"agent_id": agent_id})
        agent_info = AgentInfo(
            agent_id=agent_id,
            name=agent_name,
            description=agent_description,
            status='active',
            created_at=format_for_api(agent_row.get("agent_create_time")) if agent_row else None,
            created_by=request.created_by,
            bootstrap_active=True,
        )

        return CreateAgentResponse(
            success=True,
            agent=agent_info,
        )

    except Exception as e:
        logger.exception(f"Error creating agent: {e}")
        return CreateAgentResponse(
            success=False,
            error=str(e)
        )


@router.put("/agents/{agent_id}", response_model=UpdateAgentResponse)
async def update_agent(agent_id: str, request: UpdateAgentRequest):
    """
    Update agent information (name, description)
    """
    logger.info(f"Updating agent: {agent_id}")

    try:
        db_client = await get_db_client()
        repo = AgentRepository(db_client)

        # Check if the agent exists
        agent = await repo.get_agent(agent_id)
        if not agent:
            return UpdateAgentResponse(
                success=False,
                error=f"Agent {agent_id} not found"
            )

        # Build update data
        update_data = {}
        if request.agent_name is not None:
            update_data["agent_name"] = request.agent_name
        if request.agent_description is not None:
            update_data["agent_description"] = request.agent_description
        if request.is_public is not None:
            update_data["is_public"] = int(request.is_public)

        if not update_data:
            return UpdateAgentResponse(
                success=False,
                error="No fields to update"
            )

        # Execute update
        affected_rows = await repo.update_agent(agent_id, update_data)

        if affected_rows > 0:
            # Get the updated agent info
            updated_agent = await repo.get_agent(agent_id)
            # Check bootstrap_active (Bootstrap.md exists in workspace)
            from xyz_agent_context.settings import settings
            workspace_path = os.path.join(
                settings.base_working_path,
                f"{agent_id}_{updated_agent.created_by}"
            )
            bootstrap_active = os.path.isfile(os.path.join(workspace_path, "Bootstrap.md"))

            agent_info = AgentInfo(
                agent_id=updated_agent.agent_id,
                name=updated_agent.agent_name,
                description=updated_agent.agent_description,
                status='active',
                created_at=format_for_api(updated_agent.agent_create_time),
                is_public=updated_agent.is_public,
                created_by=updated_agent.created_by,
                bootstrap_active=bootstrap_active,
            )
            logger.info(f"Agent {agent_id} updated successfully")

            return UpdateAgentResponse(
                success=True,
                agent=agent_info,
            )
        else:
            return UpdateAgentResponse(
                success=False,
                error="No changes made"
            )

    except Exception as e:
        logger.exception(f"Error updating agent: {e}")
        return UpdateAgentResponse(
            success=False,
            error=str(e)
        )


@router.delete("/agents/{agent_id}", response_model=DeleteAgentResponse)
async def delete_agent(
    agent_id: str,
    user_id: str = Query(..., description="Operator's user ID, used for permission verification"),
):
    """
    Cascade delete an Agent and all its associated data

    Permission: Only the Agent creator (created_by == user_id) can delete.
    Deletion order is from leaf to root to ensure foreign key safety:
    1. Instance Memory dynamic tables
    2. Narrative Memory dynamic tables
    3. Jobs
    4. Instance-Narrative Links
    5. Instance subsidiary data (social_entities, awareness, rag_store, module_report_memory)
    6. Module Instances
    7. Events
    8. Narratives
    9. MCP URLs
    10. Agent Messages
    11. The Agent itself
    """
    logger.info(f"Delete agent request: agent_id={agent_id}, user_id={user_id}")

    try:
        db_client = await get_db_client()
        repo = AgentRepository(db_client)

        # 1. Permission check: only the creator can delete
        agent = await repo.get_agent(agent_id)
        if not agent:
            return DeleteAgentResponse(
                success=False,
                agent_id=agent_id,
                error=f"Agent {agent_id} not found",
            )

        if agent.created_by != user_id:
            return DeleteAgentResponse(
                success=False,
                agent_id=agent_id,
                error="Permission denied: only the creator can delete this agent",
            )

        stats: dict[str, int] = {}

        # 2. Collect all associated instance_ids
        inst_rows = await db_client.execute(
            "SELECT instance_id FROM module_instances WHERE agent_id = %s",
            (agent_id,),
            fetch=True,
        )
        instance_ids = [r["instance_id"] for r in inst_rows] if inst_rows else []

        # 3. Collect all associated narrative_ids
        nar_rows = await db_client.execute(
            "SELECT narrative_id FROM narratives WHERE agent_id = %s",
            (agent_id,),
            fetch=True,
        )
        narrative_ids = [r["narrative_id"] for r in nar_rows] if nar_rows else []

        # 4. Discover dynamic Memory tables (compatible with both MySQL and SQLite)
        is_sqlite = hasattr(db_client, '_backend') and db_client._backend and db_client._backend.dialect == "sqlite"
        if is_sqlite:
            mem_rows = await db_client.execute(
                """
                SELECT name AS tbl FROM sqlite_master
                WHERE type='table'
                  AND (name LIKE 'json_format_event_memory_%'
                       OR name LIKE 'instance_json_format_memory_%')
                """,
                params=(),
                fetch=True,
            )
        else:
            mem_rows = await db_client.execute(
                """
                SELECT TABLE_NAME AS tbl FROM information_schema.tables
                WHERE table_schema = DATABASE()
                  AND (TABLE_NAME LIKE 'json_format_event_memory_%%'
                       OR TABLE_NAME LIKE 'instance_json_format_memory_%%')
                """,
                params=(),
                fetch=True,
            )
        memory_tables = [r["tbl"] for r in mem_rows] if mem_rows else []

        # ========== Delete from leaf to root ==========

        # 4a. Instance Memory dynamic tables (by instance_id)
        if instance_ids:
            ph = ", ".join(["%s"] * len(instance_ids))
            for tbl in memory_tables:
                if tbl.startswith("instance_json_format_memory_"):
                    result = await db_client.execute(
                        f"DELETE FROM `{tbl}` WHERE instance_id IN ({ph})",
                        tuple(instance_ids),
                        fetch=False,
                    )
                    cnt = result if isinstance(result, int) else 0
                    if cnt > 0:
                        stats[tbl] = cnt

        # 4b. Narrative Memory dynamic tables (by narrative_id)
        if narrative_ids:
            ph_n = ", ".join(["%s"] * len(narrative_ids))
            for tbl in memory_tables:
                if tbl.startswith("json_format_event_memory_"):
                    result = await db_client.execute(
                        f"DELETE FROM `{tbl}` WHERE narrative_id IN ({ph_n})",
                        tuple(narrative_ids),
                        fetch=False,
                    )
                    cnt = result if isinstance(result, int) else 0
                    if cnt > 0:
                        stats[tbl] = cnt

        # 5. Jobs (by agent_id)
        result = await db_client.execute(
            "DELETE FROM instance_jobs WHERE agent_id = %s",
            (agent_id,),
            fetch=False,
        )
        cnt = result if isinstance(result, int) else 0
        if cnt > 0:
            stats["instance_jobs"] = cnt

        # 6. Instance-Narrative Links (by instance_id)
        if instance_ids:
            ph = ", ".join(["%s"] * len(instance_ids))
            result = await db_client.execute(
                f"DELETE FROM instance_narrative_links WHERE instance_id IN ({ph})",
                tuple(instance_ids),
                fetch=False,
            )
            cnt = result if isinstance(result, int) else 0
            if cnt > 0:
                stats["instance_narrative_links"] = cnt

        # 7. Instance subsidiary data (by instance_id)
        instance_sub_tables = [
            "instance_social_entities",
            "instance_awareness",
            "instance_rag_store",
            "instance_module_report_memory",
            "instance_json_format_memory",
        ]
        if instance_ids:
            ph = ", ".join(["%s"] * len(instance_ids))
            for sub_tbl in instance_sub_tables:
                try:
                    result = await db_client.execute(
                        f"DELETE FROM `{sub_tbl}` WHERE instance_id IN ({ph})",
                        tuple(instance_ids),
                        fetch=False,
                    )
                    cnt = result if isinstance(result, int) else 0
                    if cnt > 0:
                        stats[sub_tbl] = cnt
                except Exception:
                    # Table may not exist, skip
                    pass

        # 8. Module Instances (by agent_id)
        result = await db_client.execute(
            "DELETE FROM module_instances WHERE agent_id = %s",
            (agent_id,),
            fetch=False,
        )
        cnt = result if isinstance(result, int) else 0
        if cnt > 0:
            stats["module_instances"] = cnt

        # 9. Events (by agent_id)
        result = await db_client.execute(
            "DELETE FROM events WHERE agent_id = %s",
            (agent_id,),
            fetch=False,
        )
        cnt = result if isinstance(result, int) else 0
        if cnt > 0:
            stats["events"] = cnt

        # 10. Narratives (by agent_id)
        result = await db_client.execute(
            "DELETE FROM narratives WHERE agent_id = %s",
            (agent_id,),
            fetch=False,
        )
        cnt = result if isinstance(result, int) else 0
        if cnt > 0:
            stats["narratives"] = cnt

        # 11. MCP URLs (by agent_id)
        result = await db_client.execute(
            "DELETE FROM mcp_urls WHERE agent_id = %s",
            (agent_id,),
            fetch=False,
        )
        cnt = result if isinstance(result, int) else 0
        if cnt > 0:
            stats["mcp_urls"] = cnt

        # 12. Agent Messages (by agent_id)
        try:
            result = await db_client.execute(
                "DELETE FROM agent_messages WHERE agent_id = %s",
                (agent_id,),
                fetch=False,
            )
            cnt = result if isinstance(result, int) else 0
            if cnt > 0:
                stats["agent_messages"] = cnt
        except Exception:
            pass

        # 13. Workspace directory
        try:
            import os
            import shutil
            from xyz_agent_context.settings import settings
            workspace_path = os.path.join(
                settings.base_working_path, f"{agent_id}_{agent.created_by}"
            )
            if os.path.isdir(workspace_path):
                shutil.rmtree(workspace_path)
                stats["workspace_dir"] = 1
                logger.info(f"Deleted workspace: {workspace_path}")
        except Exception as e:
            logger.warning(f"Workspace cleanup failed (non-critical): {e}")

        # 14. Lark credentials + CLI profile + workspace + inbox data
        try:
            lark_cred = await db_client.get_one("lark_credentials", {"agent_id": agent_id})
            if lark_cred:
                # Remove CLI profile via the shared client — it handles HOME
                # override and keychain cleanup regardless of which bind path
                # created this credential.
                from xyz_agent_context.module.lark_module.lark_cli_client import LarkCLIClient
                from xyz_agent_context.module.lark_module._lark_workspace import cleanup_workspace
                try:
                    await LarkCLIClient().profile_remove(agent_id)
                except Exception as e:
                    logger.debug(f"profile_remove best-effort failed for {agent_id}: {e}")
                # Blow away the workspace directory (idempotent)
                cleanup_workspace(agent_id)

                # Clean up lark inbox channels
                all_members = await db_client.get("bus_channel_members", {"agent_id": agent_id})
                for m in all_members:
                    cid = m.get("channel_id", "")
                    if cid.startswith("lark_"):
                        await db_client.delete("bus_channel_members", {"channel_id": cid, "agent_id": agent_id})
                        remaining = await db_client.get("bus_channel_members", {"channel_id": cid})
                        if not remaining:
                            await db_client.delete("bus_messages", {"channel_id": cid})
                            await db_client.delete("bus_channels", {"channel_id": cid})
                # Delete credential
                result = await db_client.execute(
                    "DELETE FROM lark_credentials WHERE agent_id = %s",
                    (agent_id,), fetch=False,
                )
                cnt = result if isinstance(result, int) else 0
                if cnt > 0:
                    stats["lark_credentials"] = cnt
        except Exception as e:
            logger.warning(f"Lark cleanup failed (non-critical): {e}")

        # 15. The Agent itself
        result = await db_client.execute(
            "DELETE FROM agents WHERE agent_id = %s",
            (agent_id,),
            fetch=False,
        )
        cnt = result if isinstance(result, int) else 0
        if cnt > 0:
            stats["agents"] = cnt

        total = sum(stats.values())
        logger.info(f"Agent {agent_id} deleted, total {total} rows removed: {stats}")

        return DeleteAgentResponse(
            success=True,
            agent_id=agent_id,
            deleted_counts=stats,
        )

    except Exception as e:
        logger.exception(f"Error deleting agent {agent_id}: {e}")
        return DeleteAgentResponse(
            success=False,
            agent_id=agent_id,
            error=str(e),
        )


@router.post("/create-user", response_model=CreateUserResponse)
async def create_user(request: CreateUserRequest):
    """
    Create a new local user.

    Flow:
    1. Check if user_id already exists
    2. Create a new user in the users table
    """
    logger.info(f"Create user request for: {request.user_id}")

    try:
        db_client = await get_db_client()
        user_repo = UserRepository(db_client)

        # Check if user already exists
        existing_user = await user_repo.get_user(request.user_id)
        if existing_user:
            logger.warning(f"User {request.user_id} already exists")
            return CreateUserResponse(
                success=False,
                error="User already exists"
            )

        # Create new user
        await user_repo.add_user(
            user_id=request.user_id,
            user_type="individual",
            display_name=request.display_name or request.user_id,
        )

        logger.info(f"User {request.user_id} created successfully")
        return CreateUserResponse(
            success=True,
            user_id=request.user_id,
        )

    except Exception as e:
        logger.exception(f"Error creating user: {e}")
        return CreateUserResponse(
            success=False,
            error=str(e)
        )


@router.post("/timezone", response_model=UpdateTimezoneResponse)
async def update_timezone(request: UpdateTimezoneRequest):
    """
    Update user timezone

    Automatically called when the browser page loads to sync the user's local timezone setting.
    Timezone uses IANA format, e.g., 'Asia/Shanghai', 'America/New_York', etc.

    Args:
        request: Request body containing user_id and timezone

    Returns:
        Update result, including success status and current timezone
    """
    logger.info(f"Timezone update request: user={request.user_id}, timezone={request.timezone}")

    try:
        # Validate timezone format
        if not is_valid_timezone(request.timezone):
            logger.warning(f"Invalid timezone format: {request.timezone}")
            return UpdateTimezoneResponse(
                success=False,
                error=f"Invalid timezone format: {request.timezone}. Use IANA format like 'Asia/Shanghai'"
            )

        db_client = await get_db_client()
        user_repo = UserRepository(db_client)

        # Check if user exists
        user = await user_repo.get_user(request.user_id)
        if not user:
            logger.warning(f"User {request.user_id} not found")
            return UpdateTimezoneResponse(
                success=False,
                error="User not found"
            )

        # Update timezone
        await user_repo.update_timezone(request.user_id, request.timezone)

        logger.info(f"User {request.user_id} timezone updated to {request.timezone}")
        return UpdateTimezoneResponse(
            success=True,
            user_id=request.user_id,
            timezone=request.timezone,
        )

    except Exception as e:
        logger.exception(f"Error updating timezone: {e}")
        return UpdateTimezoneResponse(
            success=False,
            error=str(e)
        )
