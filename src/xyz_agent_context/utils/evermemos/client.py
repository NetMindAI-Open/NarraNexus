"""
@file_name: _evermemos_client.py
@author: NetMind.AI
@date: 2026-02-06
@description: EverMemOS HTTP API client (internal implementation)

Migrated from narrative/_narrative_impl/evermemos_service.py.
MemoryModule is the only public interface for memory management; this file is the internal implementation.

Features:
1. Writing: Event -> HTTP POST /api/v1/memories
2. Retrieval: Query -> HTTP GET /api/v1/memories/search -> aggregate by narrative_id
"""

from __future__ import annotations

from typing import List, Dict, Optional, TYPE_CHECKING
from datetime import datetime, timezone
import os

import httpx
from loguru import logger

# Use TYPE_CHECKING to avoid circular imports
if TYPE_CHECKING:
    from xyz_agent_context.narrative.models import NarrativeSearchResult, Event, Narrative


# Global client cache
_evermemos_clients: Dict[str, "EverMemOSClient"] = {}


def get_evermemos_client(agent_id: str, user_id: str) -> "EverMemOSClient":
    """Get or create an EverMemOS client instance"""
    key = f"{agent_id}_{user_id}"
    if key not in _evermemos_clients:
        _evermemos_clients[key] = EverMemOSClient(agent_id, user_id)
    return _evermemos_clients[key]


class EverMemOSClient:
    """
    EverMemOS HTTP API Client

    Calls EverMemOS services via HTTP API, implementing:
    1. Writing: Event -> HTTP POST /api/v1/memories
    2. Retrieval: Query -> HTTP GET /api/v1/memories/search -> aggregate by narrative_id
    """

    def __init__(
        self,
        agent_id: str,
        user_id: str,
        base_url: Optional[str] = None
    ):
        self.agent_id = agent_id
        self.user_id = user_id
        self.base_url = base_url or os.getenv("EVERMEMOS_BASE_URL", "http://localhost:1995")
        self.timeout = float(os.getenv("EVERMEMOS_TIMEOUT", "30"))

        # API endpoints
        self.memorize_url = f"{self.base_url}/api/v1/memories"
        self.search_url = f"{self.base_url}/api/v1/memories/search"
        self.conversation_meta_url = f"{self.base_url}/api/v1/memories/conversation-meta"

        # Conversation metadata cache (narrative_id -> bool)
        self._conversation_meta_saved: Dict[str, bool] = {}

    # =========================================================================
    # Write
    # =========================================================================

    async def write_event(self, event: "Event", narrative: "Narrative" = None) -> bool:
        """
        Write an Event to EverMemOS

        Flow:
        1. Ensure conversation-meta has been created (group_id = agent_id)
        2. Convert Event to message format
        3. Call POST /api/v1/memories

        After decoupling: group_id = agent_id (not narrative_id).
        Episodes are grouped per agent, not per narrative.
        narrative param kept for backward compat but only used for logging.

        Args:
            event: Narrative Event
            narrative: (deprecated) Associated Narrative, only used for logging

        Returns:
            bool: Whether the write was successful
        """
        # Ensure conversation-meta has been created (keyed by agent_id)
        await self._ensure_agent_meta()

        # Convert to message format and send
        messages = self._event_to_messages(event)

        if not messages:
            logger.debug(f"Event {event.id} has no content to write")
            return True

        logger.debug(
            f"[EverMemOS] write_event: {len(messages)} messages to write, "
            f"event={event.id}, final_output={'present' if event.final_output else 'absent'} "
            f"({len(event.final_output) if event.final_output else 0} chars)"
        )

        success = True
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for message in messages:
                try:
                    response = await client.post(
                        self.memorize_url,
                        json=message,
                        headers={"Content-Type": "application/json"}
                    )

                    if response.status_code == 200:
                        result = response.json()
                        status_info = result.get("result", {}).get("status_info", "unknown")
                        logger.info(
                            f"[EverMemOS] Event {event.id} written successfully: {status_info} "
                            f"(narrative={narrative_id})"
                        )
                    elif response.status_code == 202:
                        # 202 Accepted: request accepted, processing in background (async mode)
                        logger.info(
                            f"[EverMemOS] Event {event.id} submitted for background processing "
                            f"(narrative={narrative_id})"
                        )
                    else:
                        logger.warning(
                            f"EverMemOS write failed: HTTP {response.status_code}, "
                            f"response: {response.text[:200]}"
                        )
                        success = False

                except httpx.ConnectError:
                    logger.warning(f"Cannot connect to EverMemOS (degraded): {self.base_url}")
                    success = False
                except httpx.TimeoutException:
                    logger.warning(f"EverMemOS write timed out (degraded): {self.timeout}s")
                    success = False
                except Exception as e:
                    logger.exception(f"EverMemOS write exception: {type(e).__name__}: {e}")
                    success = False

        return success

    async def _ensure_agent_meta(self) -> bool:
        """
        Ensure conversation-meta has been created for this agent.

        After decoupling: one meta per agent_id (not per narrative_id).
        Called once per agent lifecycle.
        """
        meta_key = self.agent_id

        if self._conversation_meta_saved.get(meta_key):
            return True

        payload = {
            "version": "1.0",
            "scene": "assistant",
            "scene_desc": {},
            "name": f"Agent {self.agent_id}",
            "description": f"All conversations for agent {self.agent_id}",
            "group_id": self.agent_id,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "default_timezone": "UTC",
            "user_details": {
                self.user_id: {
                    "full_name": self.user_id,
                    "role": "user",
                    "extra": {}
                }
            },
            "tags": ["agent", self.agent_id]
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.conversation_meta_url,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )
                if response.status_code == 200:
                    self._conversation_meta_saved[meta_key] = True
                    logger.debug(f"[EverMemOS-Write] Agent meta created: agent={self.agent_id}")
                    return True
                else:
                    logger.warning(
                        f"[EverMemOS-Write] Agent meta creation failed: HTTP {response.status_code}, "
                        f"agent={self.agent_id}"
                    )
        except Exception as e:
            logger.warning(f"[EverMemOS-Write] Agent meta creation exception: {type(e).__name__}: {e}")

        self._conversation_meta_saved[meta_key] = True
        return False

    def _event_to_messages(self, event: "Event") -> List[Dict]:
        """
        Convert Event to EverMemOS message format

        Args:
            event: Narrative Event
            narrative: Associated Narrative

        Returns:
            List of messages, each corresponding to an HTTP request
        """
        messages = []
        # After decoupling: group_id = agent_id (not narrative_id)
        group_id = self.agent_id
        group_name = f"Agent {self.agent_id}"

        # Timestamp
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if event.created_at:
            timestamp = event.created_at.isoformat()

        # User input
        input_content = ""
        if event.env_context:
            input_content = event.env_context.get("input", "")

        if input_content:
            messages.append({
                "message_id": f"{event.id}_user",
                "create_time": timestamp,
                "sender": self.user_id,
                "sender_name": self.user_id,
                "role": "user",
                "type": "text",
                "content": input_content,
                "group_id": group_id,
                "group_name": group_name,
                "scene": "assistant"
            })

        # Assistant reply
        if event.final_output:
            output_timestamp = timestamp
            if event.updated_at:
                output_timestamp = event.updated_at.isoformat()

            messages.append({
                "message_id": f"{event.id}_agent",
                "create_time": output_timestamp,
                "sender": self.user_id,
                "sender_name": self.agent_id,
                "role": "assistant",
                "type": "text",
                "content": event.final_output,
                "group_id": group_id,
                "group_name": group_name,
                "scene": "assistant"
            })

        return messages

    # =========================================================================
    # Retrieval
    # =========================================================================

    async def search_narratives(
        self,
        query: str,
        top_k: int = 10,
        agent_narrative_ids: Optional[set] = None
    ) -> List["NarrativeSearchResult"]:
        """
        Retrieve relevant Narratives

        Flow:
        1. Call GET /api/v1/memories/search
        2. Aggregate scores by group_id (=narrative_id)
        3. Return NarrativeSearchResult list

        Args:
            query: Query text
            top_k: Number of results to return
            agent_narrative_ids: Set of narrative_ids owned by the current agent (for pending_messages Agent isolation)

        Returns:
            NarrativeSearchResult list, sorted by score in descending order
        """
        params = {
            "query": query,
            "top_k": top_k * 3,  # Fetch more, may reduce after aggregation
            "memory_types": "episodic_memory",
            "retrieve_method": "rrf",  # BM25 + Vector + RRF fusion
            "user_id": self.user_id,   # User isolation
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    self.search_url,
                    params=params
                )

                if response.status_code != 200:
                    logger.warning(
                        f"EverMemOS retrieval failed: HTTP {response.status_code}, "
                        f"response: {response.text[:200]}"
                    )
                    return []

                result = response.json()
                if result.get("status") != "ok":
                    logger.warning(f"EverMemOS retrieval failed: {result.get('message')}")
                    return []

                # Parse and aggregate results
                raw_memories = result.get("result", {}).get("memories", [])
                raw_scores = result.get("result", {}).get("scores", [])

                # Agent isolation: process pending_messages (raw messages not yet extracted as episodic_memory by EverMemOS)
                # Use agent_narrative_ids (narrative_ids of current agent from local DB)
                # to match pending_messages group_id, filtering out messages not belonging to the current agent
                pending_messages = result.get("result", {}).get("pending_messages", [])
                allowed_groups = self._filter_pending_messages_by_agent(
                    pending_messages, agent_narrative_ids
                )

                results = self._aggregate_by_narrative(
                    raw_memories, raw_scores, top_k, allowed_groups,
                    agent_narrative_ids=agent_narrative_ids
                )

                logger.debug(
                    f"EverMemOS retrieval complete: query='{query[:50]}...', "
                    f"returned {len(results)} narratives"
                )
                return results

        except httpx.ConnectError:
            logger.exception(f"Cannot connect to EverMemOS: {self.base_url}")
            return []
        except httpx.TimeoutException:
            logger.warning(f"EverMemOS retrieval timed out (degraded): {self.timeout}s")
            return []
        except Exception as e:
            logger.exception(f"EverMemOS retrieval exception: {type(e).__name__}: {e}")
            return []

    async def search_episodes(
        self,
        query: str,
        top_k: int = 20,
    ) -> List["EpisodeResult"]:
        """
        Pure memory retrieval — flat list of relevant episodes.

        After decoupling: searches within group_id = agent_id.
        No narrative grouping. No aggregation. No top_k × 3 hack.

        Args:
            query: Search query text
            top_k: Number of episodes to return

        Returns:
            List of EpisodeResult, sorted by relevance score
        """
        from xyz_agent_context.narrative.models import EpisodeResult
        import time as _time

        _start = _time.monotonic()

        params = {
            "query": query,
            "top_k": top_k,
            "memory_types": "episodic_memory",
            "retrieve_method": "rrf",
            "group_id": self.agent_id,
            "user_id": self.user_id,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    self.search_url,
                    params=params
                )

                if response.status_code != 200:
                    logger.warning(
                        f"[EverMemOS-Search] Failed: HTTP {response.status_code}, "
                        f"response: {response.text[:200]}"
                    )
                    return []

                result = response.json()
                if result.get("status") != "ok":
                    logger.warning(f"[EverMemOS-Search] Failed: {result.get('message')}")
                    return []

                # Parse into flat episode list (no narrative grouping)
                raw_memories = result.get("result", {}).get("memories", [])
                raw_scores = result.get("result", {}).get("scores", [])

                episodes = self._parse_flat_episodes(raw_memories, raw_scores, top_k)

                elapsed = _time.monotonic() - _start
                logger.info(
                    f"[EverMemOS-Search] query='{query[:50]}...', "
                    f"returned {len(episodes)} episodes in {elapsed:.1f}s"
                )
                return episodes

        except httpx.ConnectError:
            logger.warning(f"[EverMemOS-Search] Cannot connect (degraded): {self.base_url}")
            return []
        except httpx.TimeoutException:
            logger.warning(f"[EverMemOS-Search] Timed out (degraded): {self.timeout}s")
            return []
        except Exception as e:
            logger.exception(f"[EverMemOS-Search] Exception: {type(e).__name__}: {e}")
            return []

    def _parse_flat_episodes(
        self,
        raw_memories: List[Dict],
        raw_scores: List[Dict],
        top_k: int,
    ) -> List["EpisodeResult"]:
        """
        Parse EverMemOS response into a flat list of EpisodeResult.

        No narrative grouping — just episodes ranked by score.
        """
        from xyz_agent_context.narrative.models import EpisodeResult

        # Collect all episodes with scores
        all_episodes = []

        # Build score lookup: flatten all group scores into one list
        score_by_group: Dict[str, List[float]] = {}
        for score_dict in raw_scores:
            if not isinstance(score_dict, dict):
                continue
            for group_id, scores in score_dict.items():
                if scores and isinstance(scores, list):
                    score_by_group.setdefault(group_id, []).extend(
                        float(s) for s in scores if s is not None
                    )

        # Parse episodes with their scores
        for group_dict in raw_memories:
            if not isinstance(group_dict, dict):
                continue
            for group_id, episodes in group_dict.items():
                if not episodes or not isinstance(episodes, list):
                    continue
                group_scores = score_by_group.get(group_id, [])
                for i, episode_data in enumerate(episodes):
                    if not isinstance(episode_data, dict):
                        continue
                    episode_text = episode_data.get("episode", "")
                    summary = episode_data.get("summary", "")
                    score = group_scores[i] if i < len(group_scores) else 0.0

                    if episode_text or summary:
                        all_episodes.append(EpisodeResult(
                            episode_text=episode_text,
                            summary=summary,
                            score=score,
                            timestamp=str(episode_data.get("timestamp", "")),
                        ))

        # Sort by score descending, return top_k
        all_episodes.sort(key=lambda e: e.score, reverse=True)
        return all_episodes[:top_k]

    # =========================================================================
    # Legacy methods (to be removed after full decoupling — Step G)
    # =========================================================================

    def _filter_pending_messages_by_agent(
        self,
        pending_messages: List[Dict],
        agent_narrative_ids: Optional[set] = None
    ) -> Optional[set]:
        """
        Filter group_ids belonging to the current agent from pending_messages

        pending_messages are raw messages not yet processed into episodic_memory by EverMemOS.
        Since the EverMemOS search API filters by user_id, messages sent by the agent
        (user_id=agent_{agent_id}) do not appear in search results, so the sender
        field cannot be used to determine whether a group belongs to the current agent.

        Alternative approach: use agent_narrative_ids (the set of narrative_ids owned by
        the current agent, queried from the local database) to determine whether the
        group_id in pending_messages belongs to the current agent.

        Args:
            pending_messages: List of pending_messages returned by EverMemOS
            agent_narrative_ids: Set of narrative_ids owned by the current agent (queried from local DB)

        Returns:
            If there are pending_messages, returns the set of group_ids belonging to the current agent;
            if there are no pending_messages, returns None (indicating no filtering based on pending_messages is needed)
        """
        if not pending_messages:
            return None

        allowed_groups: set = set()
        all_groups: set = set()

        for msg in pending_messages:
            if not isinstance(msg, dict):
                continue

            group_id = msg.get("group_id")
            if not group_id:
                continue

            all_groups.add(group_id)

            # Use agent_narrative_ids to determine if group_id belongs to the current agent
            # group_id in EverMemOS corresponds to narrative_id
            if agent_narrative_ids and group_id in agent_narrative_ids:
                allowed_groups.add(group_id)

        # Log filtering results
        filtered_count = len(all_groups) - len(allowed_groups)
        if filtered_count > 0:
            logger.info(
                f"[EverMemOS] Agent isolation (pending_messages): filtered out {filtered_count} narratives "
                f"not belonging to agent={self.agent_id} (all={len(all_groups)}, allowed={len(allowed_groups)})"
            )
        elif all_groups:
            logger.debug(
                f"[EverMemOS] Agent isolation (pending_messages): all {len(all_groups)} groups passed"
            )

        return allowed_groups

    def _aggregate_by_narrative(
        self,
        raw_memories: List[Dict],
        raw_scores: List[Dict],
        top_k: int,
        allowed_groups: Optional[set] = None,
        agent_narrative_ids: Optional[set] = None
    ) -> List["NarrativeSearchResult"]:
        """
        Aggregate retrieval results by narrative_id (group_id)

        EverMemOS response format:
        {
            "memories": [
                {"group_id_1": [{episode1}, {episode2}, ...]},
                {"group_id_2": [...]}
            ],
            "scores": [
                {"group_id_1": [0.85, 0.72, ...]},
                {"group_id_2": [...]}
            ]
        }

        Aggregation strategy:
        - Score: take the highest episode score for each narrative
        - Summary: extract episode summaries under each narrative (added in Phase 1)

        RRF score normalization:
        RRF (Reciprocal Rank Fusion) scores are typically small (~0.01-0.1) because the formula is 1/(k+rank)
        To be compatible with vector similarity thresholds (0-1 range), Min-Max normalization is needed:
        - normalized_score = (score - min_score) / (max_score - min_score)
        - Then mapped to [0.3, 0.85] range: highest score can match directly but not too aggressively

        Agent isolation:
        - Filter episodic_memory via agent_narrative_ids, keeping only the current agent's narratives
        - Filter pending_messages derived results via allowed_groups

        Args:
            raw_memories: EverMemOS memories field
            raw_scores: EverMemOS scores field
            top_k: Number of results to return
            allowed_groups: Set of allowed group_ids filtered from pending_messages,
                          None means no filtering based on pending_messages

        Returns:
            NarrativeSearchResult list (including episode_summaries and episode_contents)
        """
        narrative_scores: Dict[str, float] = {}
        # Phase 1: extract episode summaries for each narrative
        narrative_summaries: Dict[str, List[str]] = {}
        # Phase 4: extract episode contents for each narrative (for short-term memory deduplication)
        narrative_contents: Dict[str, List[str]] = {}

        # Extract scores from the scores field
        for score_dict in raw_scores:
            if not isinstance(score_dict, dict):
                continue

            for group_id, scores in score_dict.items():
                if not scores or not isinstance(scores, list):
                    continue

                # Take the highest score under this narrative
                try:
                    max_score = max(float(s) for s in scores if s is not None)
                    narrative_scores[group_id] = max(
                        narrative_scores.get(group_id, 0.0),
                        max_score
                    )
                except (ValueError, TypeError):
                    continue

        # Phase 1: extract episode summaries from the memories field
        # Phase 4: also extract episode contents (for short-term memory deduplication)
        # EverMemOS memories format: [{"group_id": [episode1, episode2, ...]}, ...]
        # episode format: {"summary": "...", "episode": "...", "participants": [...], ...}
        filtered_groups = set()  # Track group_ids filtered out by Agent isolation
        for group_dict in raw_memories:
            if not isinstance(group_dict, dict):
                continue

            for group_id, episodes in group_dict.items():
                if not episodes or not isinstance(episodes, list):
                    continue

                # Agent isolation: check if group_id is in agent_narrative_ids
                # Uses the same logic as Layer 2 (pending_messages)
                if agent_narrative_ids and group_id not in agent_narrative_ids:
                    filtered_groups.add(group_id)
                    continue

                summaries = []
                contents = []  # Phase 4: collect raw episode contents
                for episode_data in episodes:
                    if not isinstance(episode_data, dict):
                        continue

                    # Phase 4: extract raw episode content (for deduplication comparison)
                    # The field name returned by EverMemOS is "episode", not "content"
                    episode_content = episode_data.get("episode", "")
                    if episode_content:
                        contents.append(episode_content)

                    # Prefer summary, fall back to episode (truncated)
                    summary = episode_data.get("summary", "")
                    if not summary:
                        if episode_content:
                            # Truncate overly long episodes
                            summary = episode_content[:200] + "..." if len(episode_content) > 200 else episode_content
                    if summary:
                        summaries.append(summary)

                if summaries:
                    # Merge into existing summaries (may come from multiple group_dicts)
                    if group_id not in narrative_summaries:
                        narrative_summaries[group_id] = []
                    narrative_summaries[group_id].extend(summaries)

                # Phase 4: merge contents
                if contents:
                    if group_id not in narrative_contents:
                        narrative_contents[group_id] = []
                    narrative_contents[group_id].extend(contents)

        # Agent isolation: remove fully filtered groups from narrative_scores
        # All episodes in these groups do not belong to the current agent
        for group_id in filtered_groups:
            if group_id in narrative_scores:
                del narrative_scores[group_id]

        if filtered_groups:
            logger.info(
                f"[EverMemOS] Agent isolation (episodic_memory): filtered out {len(filtered_groups)} narratives "
                f"not belonging to agent={self.agent_id}"
            )

        # Agent isolation: filter based on allowed_groups from pending_messages
        # When memories is empty but pending_messages has data, allowed_groups restricts the allowed group_ids
        if allowed_groups is not None:
            groups_to_remove = [
                gid for gid in narrative_scores if gid not in allowed_groups
            ]
            for gid in groups_to_remove:
                del narrative_scores[gid]

        # If scores is empty, use allowed_groups as candidates (from pending_messages)
        # If allowed_groups is also empty or None, return no results
        PENDING_ONLY_DEFAULT_SCORE = 0.03  # pending_messages have no semantic score, assign low score for LLM judgment
        # After RRF x10 mapping = 0.30, well below the high_confidence threshold (0.70)
        if not narrative_scores:
            if allowed_groups:
                for group_id in allowed_groups:
                    narrative_scores[group_id] = PENDING_ONLY_DEFAULT_SCORE
                logger.debug(
                    f"[EverMemOS] Using {len(allowed_groups)} narratives from pending_messages "
                    f"belonging to agent={self.agent_id} (default_score={PENDING_ONLY_DEFAULT_SCORE})"
                )
            elif allowed_groups is None:
                # allowed_groups is None means no pending_messages
                # Try extracting from raw_memories (legacy logic, for compatibility)
                for group_dict in raw_memories:
                    if not isinstance(group_dict, dict):
                        continue
                    for group_id in group_dict.keys():
                        narrative_scores[group_id] = 1.0

        # RRF score proportional mapping
        #
        # RRF formula: score = sum(1 / (k + rank)), k is typically 60
        # Typical score ranges:
        #   - High match (keyword + semantic both hit): 0.05 - 0.1+
        #   - Medium match: 0.03 - 0.05
        #   - Low match (nearly irrelevant): 0.01 - 0.03
        #
        # Proportional mapping strategy: normalized = raw_score * SCALE_FACTOR
        # SCALE_FACTOR = 10, after mapping:
        #   - 0.1 -> 1.0 (ultra-high match, capped at 0.95)
        #   - 0.07 -> 0.7 (high confidence threshold)
        #   - 0.05 -> 0.5 (medium match, goes to LLM judgment)
        #   - 0.016 -> 0.16 (low match, goes to create new Narrative)
        #
        # This preserves the absolute meaning of scores; low scores won't be incorrectly inflated

        RRF_SCALE_FACTOR = 10.0  # Proportional scaling factor
        RRF_MAX_SCORE = 0.95     # Maximum score cap

        if narrative_scores:
            raw_scores_values = list(narrative_scores.values())
            min_score = min(raw_scores_values)
            max_score = max(raw_scores_values)

            # Proportional mapping
            for narr_id in narrative_scores:
                raw = narrative_scores[narr_id]
                scaled = raw * RRF_SCALE_FACTOR
                # Cap maximum score at 0.95
                narrative_scores[narr_id] = min(scaled, RRF_MAX_SCORE)

            logger.info(
                f"[EverMemOS] RRF score proportional mapping (x{RRF_SCALE_FACTOR}): "
                f"raw [{min_score:.4f}, {max_score:.4f}] -> "
                f"[{min_score * RRF_SCALE_FACTOR:.2f}, {min(max_score * RRF_SCALE_FACTOR, RRF_MAX_SCORE):.2f}]"
            )

        # Sort and build results
        sorted_narratives = sorted(
            narrative_scores.items(),
            key=lambda x: x[1],
            reverse=True
        )

        # Lazy import to avoid circular dependencies
        from xyz_agent_context.narrative.models import NarrativeSearchResult

        # Get the episode count limit per Narrative from config
        from xyz_agent_context.narrative import config
        max_summaries = config.EVERMEMOS_EPISODE_SUMMARIES_PER_NARRATIVE
        max_contents = config.EVERMEMOS_EPISODE_CONTENTS_PER_NARRATIVE

        results = []
        for rank, (narr_id, score) in enumerate(sorted_narratives[:top_k], 1):
            # Phase 1: get episode summaries for this narrative (for Auxiliary Narratives)
            summaries = narrative_summaries.get(narr_id, [])[:max_summaries]
            # Long-term memory: get episode contents for this narrative (for current Narrative long-term memory)
            contents = narrative_contents.get(narr_id, [])[:max_contents]
            results.append(NarrativeSearchResult(
                narrative_id=narr_id,
                similarity_score=score,
                rank=rank,
                episode_summaries=summaries,
                episode_contents=contents
            ))

        # Phase 1 & 4: log extracted summaries and contents statistics
        total_summaries = sum(len(narrative_summaries.get(r.narrative_id, [])) for r in results)
        total_contents = sum(len(narrative_contents.get(r.narrative_id, [])) for r in results)
        if total_summaries > 0 or total_contents > 0:
            logger.info(
                f"[EverMemOS] Extracted episode data: "
                f"{len(results)} narratives, {total_summaries} summaries, {total_contents} raw contents"
            )

        return results
