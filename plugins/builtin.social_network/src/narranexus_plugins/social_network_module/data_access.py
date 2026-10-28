"""
@file_name: data_access.py
@author: Bin Liang
@date: 2026-09-04
@description: builtin.social_network's AgentDataStore bodies: entity writes/reads, network search, stats, create_agent.

Registered into ``agent.capabilities.data_access`` by the builtin.social_network manifest
Handlers take the
store's db client first; module internals are imported inside each handler so
registering the contribution stays free of the module's import cost.
"""
from __future__ import annotations

from typing import Any, Optional

from narranexus.contracts.data_access import DataAccessSpec
from narranexus.kernel.plugins.registry import Contribution


async def _resolve(db: Any, agent_id: str):
    """The agent's SocialNetworkModule instance and a temp module bound to it —
    the same (instance lookup + module construction) the backend social routes do.

    Returns (module, instance_id, None) or (None, None, failure_dict) where the
    failure dict is the seam's own ``message``-shaped dict, so a caller never
    sees an exception escape (the DirectStore invariant)."""
    from loguru import logger

    from narranexus_plugins.social_network_module import SocialNetworkModule, social_instance_not_found_msg
    from narranexus.platform.repository import InstanceRepository

    try:
        instances = await InstanceRepository(db).get_by_agent(agent_id=agent_id, module_class="SocialNetworkModule")
        if not instances:
            return None, None, {"success": False, "message": social_instance_not_found_msg(agent_id)}
        instance_id = instances[0].instance_id
        module = SocialNetworkModule(agent_id=agent_id, database_client=db, instance_id=instance_id)
        return module, instance_id, None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[social] instance resolution failed for {agent_id}: {e}")
        return None, None, {"success": False, "message": f"Error: {e}"}


async def extract_entity_info(db: Any, agent_id: str, entity_id: str, updates: dict, update_mode: str) -> dict:
    module, instance_id, err = await _resolve(db, agent_id)
    if err is not None:
        return err
    return await module.extract_and_update_entity_info(entity_id=entity_id, instance_id=instance_id, updates=updates, update_mode=update_mode)


async def merge_entities(db: Any, agent_id: str, source_entity_id: str, target_entity_id: str, keep_target_name: bool) -> dict:
    module, instance_id, err = await _resolve(db, agent_id)
    if err is not None:
        return err
    return await module.merge_entities(
        source_entity_id=source_entity_id, target_entity_id=target_entity_id, instance_id=instance_id, keep_target_name=keep_target_name
    )


async def delete_entity(db: Any, agent_id: str, entity_id: str) -> dict:
    module, instance_id, err = await _resolve(db, agent_id)
    if err is not None:
        return err
    return await module.delete_entity(entity_id=entity_id, instance_id=instance_id)


async def search_social_network(db: Any, agent_id: str, search_keyword: str, search_type: str, top_k: int) -> dict:
    module, instance_id, err = await _resolve(db, agent_id)
    if err is not None:
        return {**err, "results": []}  # search tool's no-instance shape
    return await module.search_network(search_keyword=search_keyword, instance_id=instance_id, search_type=search_type, top_k=top_k)


async def get_contact_info(db: Any, agent_id: str, entity_id: str) -> dict:
    from narranexus_plugins.social_network_module import format_contact_result

    module, instance_id, err = await _resolve(db, agent_id)
    if err is not None:
        return err  # get_contact_info's no-instance shape (no results key)
    recall = await module.recall_entity_info(entity_id, instance_id)
    return format_contact_result(entity_id, recall)


async def get_agent_social_stats(db: Any, agent_id: str, sort_by: str, top_k: int, filter_tags: Optional[list]) -> dict:
    from narranexus_plugins.social_network_module import format_stats_result

    module, instance_id, err = await _resolve(db, agent_id)
    if err is not None:
        return {**err, "results": []}  # stats tool's no-instance shape
    stats = await module.get_agent_stats(instance_id=instance_id, sort_by=sort_by, top_k=top_k, filter_tags=filter_tags)
    return format_stats_result(sort_by, stats)


async def create_agent(db: Any, creator_agent_id: str, new_agent_id: str, agent_name: str, awareness: str, agent_description: str) -> dict:
    from loguru import logger

    from narranexus.platform.bootstrap.provision import provision_new_agent
    from narranexus_plugins.social_network_module import (
        CREATE_AGENT_NO_OWNER_MSG,
        create_agent_text_reject,
        default_created_by_description,
        format_create_agent_success,
    )
    from narranexus.platform.repository import AgentRepository
    from narranexus.platform.schema import normalize_agent_text

    caller = await AgentRepository(db).get_agent(creator_agent_id)
    if not caller or not caller.created_by:
        return {"success": False, "message": CREATE_AGENT_NO_OWNER_MSG}
    agent_name = normalize_agent_text(agent_name)
    agent_description = normalize_agent_text(agent_description)
    refusal = create_agent_text_reject(agent_name, agent_description)
    if refusal:
        return {"success": False, "message": refusal}
    result = await provision_new_agent(
        db,
        agent_id=new_agent_id,
        user_id=caller.created_by,
        agent_name=agent_name,
        agent_description=agent_description or default_created_by_description(caller.agent_name or creator_agent_id),
        awareness=awareness,
    )
    logger.info(f"Created agent {new_agent_id} ('{agent_name}') for owner {caller.created_by}")
    return format_create_agent_success(agent_name, new_agent_id, result.warnings)


_HANDLERS = {
    "extract_entity_info": extract_entity_info,
    "merge_entities": merge_entities,
    "delete_entity": delete_entity,
    "search_social_network": search_social_network,
    "get_contact_info": get_contact_info,
    "get_agent_social_stats": get_agent_social_stats,
    "create_agent": create_agent,
}
DATA_ACCESS = tuple(Contribution(n, (lambda n=n, h=h: DataAccessSpec(n, h))) for n, h in _HANDLERS.items())

__all__ = ["DATA_ACCESS", *_HANDLERS]
