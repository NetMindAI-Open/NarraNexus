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
from fastapi import APIRouter, Query
from loguru import logger

from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.utils import format_for_api
from xyz_agent_context.repository import AgentRepository, UserRepository
from xyz_agent_context.schema import (
    LoginRequest,
    LoginResponse,
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
from xyz_agent_context.utils import is_valid_timezone
from xyz_agent_context.settings import settings as app_settings


router = APIRouter()


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """
    Login with user_id
    Only allows users that exist in the database users table to login
    """
    logger.info(f"Login attempt for user: {request.user_id}")

    try:
        db_client = await get_db_client()
        user_repo = UserRepository(db_client)

        # Verify user exists in the users table
        user = await user_repo.get_user(request.user_id)

        if user:
            # Update last login time
            await user_repo.update_last_login(request.user_id)
            logger.info(f"User {request.user_id} logged in successfully")
            return LoginResponse(
                success=True,
                user_id=request.user_id,
            )
        else:
            # User does not exist, deny login
            logger.warning(f"User {request.user_id} not found in users table")
            return LoginResponse(
                success=False,
                error="User not found. Please contact administrator to create an account."
            )

    except Exception as e:
        logger.error(f"Error during login: {e}")
        return LoginResponse(
            success=False,
            error=str(e)
        )


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
    logger.info(f"Getting agents list for user: {user_id}")

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

        logger.info(f"Found {len(agents)} agents for user {user_id}")

        return AgentListResponse(
            success=True,
            agents=agents,
            count=len(agents),
        )

    except Exception as e:
        logger.error(f"Error getting agents: {e}")
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

        # Compute workspace path (used by bootstrap + matrix registration)
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

        # Register agent on NexusMatrix Server (non-fatal)
        try:
            from xyz_agent_context.module.matrix_module._matrix_credential_manager import (
                ensure_agent_registered,
            )
            cred = await ensure_agent_registered(db=db_client, agent_id=agent_id)
            if cred:
                logger.info(f"Agent {agent_id} registered on NexusMatrix: {cred.matrix_user_id}")
            else:
                logger.warning(f"Agent {agent_id} Matrix registration failed (NexusMatrix may be down)")
        except Exception as matrix_err:
            logger.warning(f"Matrix registration error (non-fatal): {matrix_err}")

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
        logger.error(f"Error creating agent: {e}")
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

            # Sync profile to NexusMatrix (non-blocking, don't fail the request)
            if request.agent_name is not None or request.agent_description is not None:
                try:
                    from xyz_agent_context.module.matrix_module._matrix_credential_manager import MatrixCredentialManager
                    from xyz_agent_context.module.matrix_module.matrix_client import NexusMatrixClient

                    cred_mgr = MatrixCredentialManager(db_client)
                    cred = await cred_mgr.get_credential(agent_id)
                    if cred:
                        # Get NexusMatrix agent_id from profile
                        client = NexusMatrixClient(server_url=cred.server_url)
                        try:
                            profile = await client.get_my_profile(api_key=cred.api_key)
                            nexus_agent_id = profile.get("agent_id", "") if profile else ""
                            if nexus_agent_id:
                                matrix_updates = {}
                                if request.agent_name is not None:
                                    matrix_updates["agent_name"] = request.agent_name
                                if request.agent_description is not None:
                                    matrix_updates["description"] = request.agent_description
                                await client.update_agent_profile(
                                    api_key=cred.api_key,
                                    agent_id=nexus_agent_id,
                                    updates=matrix_updates,
                                )
                                logger.info(f"Synced agent profile to NexusMatrix: {nexus_agent_id}")
                        finally:
                            await client.close()
                except Exception as e:
                    logger.warning(f"Failed to sync profile to NexusMatrix (non-critical): {e}")

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
        logger.error(f"Error updating agent: {e}")
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

        # 13. Matrix credentials + NexusMatrix Server deregistration
        try:
            from xyz_agent_context.module.matrix_module._matrix_credential_manager import MatrixCredentialManager
            from xyz_agent_context.module.matrix_module.matrix_client import NexusMatrixClient

            cred_mgr = MatrixCredentialManager(db_client)
            cred = await cred_mgr.get_credential(agent_id)
            if cred:
                # Deactivate on NexusMatrix Server (best-effort)
                try:
                    client = NexusMatrixClient(server_url=cred.server_url)
                    try:
                        profile = await client.get_my_profile(api_key=cred.api_key)
                        if profile and profile.get("agent_id"):
                            await client.update_agent_profile(
                                api_key=cred.api_key,
                                agent_id=profile["agent_id"],
                                updates={"status": "inactive"},
                            )
                            logger.info(f"Deactivated agent on NexusMatrix: {profile['agent_id']}")
                    finally:
                        await client.close()
                except Exception as e:
                    logger.warning(f"Failed to deactivate on NexusMatrix (non-critical): {e}")

                # Delete local credentials
                await cred_mgr.delete(agent_id)
                stats["matrix_credentials"] = 1
        except Exception as e:
            logger.warning(f"Matrix credential cleanup failed (non-critical): {e}")

        # 14. Workspace directory
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
        logger.error(f"Error deleting agent {agent_id}: {e}")
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
        logger.error(f"Error creating user: {e}")
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
        logger.error(f"Error updating timezone: {e}")
        return UpdateTimezoneResponse(
            success=False,
            error=str(e)
        )
