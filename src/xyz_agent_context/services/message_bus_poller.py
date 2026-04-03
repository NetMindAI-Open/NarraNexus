"""
@file_name: message_bus_poller.py
@author: Bin Liang
@date: 2026-04-02
@description: Lightweight poller for MessageBus message delivery

Periodically checks for pending messages across all registered agents
and delivers them. Can be integrated into ModulePoller later or run
as a standalone service.

Design:
- For each agent, fetches pending (unprocessed) messages
- Logs delivery and acknowledges processed messages
- Records failures so poison messages are eventually skipped (>= 3 failures)
"""

from __future__ import annotations

from typing import List

from loguru import logger

from xyz_agent_context.message_bus.message_bus_service import MessageBusService


async def poll_message_bus(
    bus: MessageBusService,
    agent_ids: List[str],
) -> int:
    """
    Poll the MessageBus for pending messages and process them.

    Called periodically to deliver pending messages to agents.
    For now, "processing" means logging and acknowledging; actual agent
    callback integration will be added when wired into the AgentRuntime.

    Args:
        bus: A MessageBusService implementation.
        agent_ids: List of agent IDs to poll for.

    Returns:
        Total number of messages processed across all agents.
    """
    total_processed = 0

    for agent_id in agent_ids:
        try:
            pending = await bus.get_pending_messages(agent_id)
            if not pending:
                continue

            logger.info(
                f"MessageBus: {len(pending)} pending message(s) for {agent_id}"
            )

            for msg in pending:
                try:
                    # Process message (log for now; future: trigger agent callback)
                    logger.info(
                        f"Delivering message {msg.message_id} "
                        f"from {msg.from_agent} to {agent_id} "
                        f"in channel {msg.channel_id}"
                    )

                    # Acknowledge processing up to this message's timestamp
                    await bus.ack_processed(
                        agent_id,
                        msg.channel_id,
                        msg.created_at,
                    )
                    total_processed += 1

                except Exception as e:
                    logger.error(
                        f"Failed to deliver message {msg.message_id} "
                        f"to {agent_id}: {e}"
                    )
                    await bus.record_failure(msg.message_id, agent_id, str(e))

        except Exception as e:
            logger.error(f"MessageBus poll error for agent {agent_id}: {e}")

    return total_processed
