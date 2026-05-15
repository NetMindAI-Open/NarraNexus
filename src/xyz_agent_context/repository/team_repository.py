"""
@file_name: team_repository.py
@author: NetMind.AI
@date: 2026-05-08
@description: Team & TeamMember repositories

Subproject 1: Team Membership
"""

import secrets
from typing import Dict, Any, List, Optional
from loguru import logger

from .base import BaseRepository
from xyz_agent_context.schema.team_schema import Team, TeamMember


class TeamRepository(BaseRepository[Team]):
    table_name = "teams"
    id_field = "team_id"

    @staticmethod
    def gen_team_id() -> str:
        return f"team_{secrets.token_hex(6)}"

    async def create_team(
        self,
        owner_user_id: str,
        name: str,
        description: Optional[str] = None,
        color: Optional[str] = None,
        source: str = "user",
        intro_md: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> Team:
        team = Team(
            team_id=team_id or self.gen_team_id(),
            owner_user_id=owner_user_id,
            name=name,
            description=description,
            color=color,
            source=source,
            intro_md=intro_md,
        )
        await self.insert(team)
        return team

    async def get_team(self, team_id: str) -> Optional[Team]:
        row = await self._db.get_one(self.table_name, {"team_id": team_id})
        return self._row_to_entity(row) if row else None

    async def list_teams_by_owner(self, owner_user_id: str) -> List[Team]:
        rows = await self._db.get(
            self.table_name, {"owner_user_id": owner_user_id}, order_by="created_at ASC"
        )
        return [self._row_to_entity(r) for r in rows]

    async def update_team(self, team_id: str, updates: Dict[str, Any]) -> int:
        if not updates:
            return 0
        return await self._db.update(self.table_name, {"team_id": team_id}, updates)

    async def delete_team(self, team_id: str) -> int:
        return await self._db.delete(self.table_name, {"team_id": team_id})

    def _row_to_entity(self, row: Dict[str, Any]) -> Team:
        return Team(
            id=row.get("id"),
            team_id=row["team_id"],
            owner_user_id=row["owner_user_id"],
            name=row["name"],
            description=row.get("description"),
            color=row.get("color"),
            source=row.get("source") or "user",
            intro_md=row.get("intro_md"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    def _entity_to_row(self, entity: Team) -> Dict[str, Any]:
        return {
            "team_id": entity.team_id,
            "owner_user_id": entity.owner_user_id,
            "name": entity.name,
            "description": entity.description,
            "color": entity.color,
            "source": entity.source,
            "intro_md": entity.intro_md,
        }


class TeamMemberRepository(BaseRepository[TeamMember]):
    table_name = "team_members"
    id_field = "id"

    async def add_member(self, team_id: str, agent_id: str) -> bool:
        existing = await self._db.get_one(
            self.table_name, {"team_id": team_id, "agent_id": agent_id}
        )
        if existing:
            return False
        await self._db.insert(self.table_name, {"team_id": team_id, "agent_id": agent_id})
        return True

    async def remove_member(self, team_id: str, agent_id: str) -> int:
        return await self._db.delete(
            self.table_name, {"team_id": team_id, "agent_id": agent_id}
        )

    async def list_members_by_team(self, team_id: str) -> List[str]:
        rows = await self._db.get(self.table_name, {"team_id": team_id})
        return [r["agent_id"] for r in rows]

    async def list_teams_by_agent(self, agent_id: str) -> List[str]:
        rows = await self._db.get(self.table_name, {"agent_id": agent_id})
        return [r["team_id"] for r in rows]

    async def list_team_mates(
        self, agent_id: str, exclude_self: bool = True
    ) -> List[Dict[str, str]]:
        """Return [{agent_id, team_id}] of agents who share at least one team with the given agent."""
        team_ids = await self.list_teams_by_agent(agent_id)
        if not team_ids:
            return []

        results: List[Dict[str, str]] = []
        seen: set = set()
        for tid in team_ids:
            rows = await self._db.get(self.table_name, {"team_id": tid})
            for r in rows:
                aid = r["agent_id"]
                if exclude_self and aid == agent_id:
                    continue
                key = (aid, tid)
                if key in seen:
                    continue
                seen.add(key)
                results.append({"agent_id": aid, "team_id": tid})
        return results

    async def remove_all_members(self, team_id: str) -> int:
        return await self._db.delete(self.table_name, {"team_id": team_id})

    async def remove_agent_from_all_teams(self, agent_id: str) -> int:
        return await self._db.delete(self.table_name, {"agent_id": agent_id})

    def _row_to_entity(self, row: Dict[str, Any]) -> TeamMember:
        return TeamMember(
            id=row.get("id"),
            team_id=row["team_id"],
            agent_id=row["agent_id"],
            joined_at=row.get("joined_at"),
        )

    def _entity_to_row(self, entity: TeamMember) -> Dict[str, Any]:
        return {
            "team_id": entity.team_id,
            "agent_id": entity.agent_id,
        }
