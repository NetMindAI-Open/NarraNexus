/**
 * @file_name: agentGroupUtils.ts
 * @author:
 * @date: 2026-06-10
 * @description: Pure helpers for M1 grouped sidebar. Extracted from AgentList
 * so they can be unit-tested without a DOM. Contains grouping derivation,
 * unread aggregation, and localStorage collapse-state persistence.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Minimal agent shape required by the grouping helpers. */
export interface GroupableAgent {
  agent_id: string;
  name?: string;
}

/** Minimal team shape required by the grouping helpers. */
export interface GroupableTeam {
  team: { team_id: string; name: string; color?: string | null };
  member_agent_ids: string[];
}

/** One section in the grouped list. teamId=null means Ungrouped. */
export interface AgentGroup<A extends GroupableAgent = GroupableAgent> {
  teamId: string | null;
  teamName: string;
  teamColor: string | null;
  agents: A[];
}

/** The JSON blob stored under SIDEBAR_COLLAPSED_KEY. */
export type CollapsedState = Record<string, boolean>;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** localStorage key for persisted collapse state (per-team map). */
export const SIDEBAR_COLLAPSED_KEY = 'sidebar_team_collapsed_v1';

// ---------------------------------------------------------------------------
// buildAgentGroups
// ---------------------------------------------------------------------------

/**
 * Derives ordered section groups from a flat agent list + team store data.
 *
 * Rules:
 * - Sections follow teamsStore order.
 * - Agents in multiple teams appear in EVERY team they belong to.
 * - An "Ungrouped" section (teamId=null) is always appended last, even if
 *   empty — it anchors agents not belonging to any team and makes the
 *   group count predictable for callers.
 * - Within each section, agents maintain the same relative order as in the
 *   input `agents` array (i.e. store order, typically creation order).
 */
export function buildAgentGroups<A extends GroupableAgent>(
  agents: A[],
  teams: GroupableTeam[],
): AgentGroup<A>[] {
  const memberSet = new Set<string>();
  teams.forEach((t) => t.member_agent_ids.forEach((id) => memberSet.add(id)));

  const groups: AgentGroup<A>[] = teams.map((t) => ({
    teamId: t.team.team_id,
    teamName: t.team.name,
    teamColor: t.team.color ?? null,
    // Preserve input (store) order within the section.
    agents: agents.filter((a) => t.member_agent_ids.includes(a.agent_id)),
  }));

  // Always include Ungrouped, even when empty.
  const ungroupedAgents = agents.filter((a) => !memberSet.has(a.agent_id));
  groups.push({
    teamId: null,
    teamName: 'Ungrouped',
    teamColor: null,
    agents: ungroupedAgents,
  });

  return groups;
}

// ---------------------------------------------------------------------------
// aggregateSectionUnread
// ---------------------------------------------------------------------------

/**
 * Sums the unread counts of all agents in a section.
 * Callers supply a `getUnread(agentId)` callback that already encodes
 * the "active agent always = 0" invariant from AgentList.
 */
export function aggregateSectionUnread<A extends GroupableAgent>(
  agents: A[],
  getUnread: (agentId: string) => number,
): number {
  return agents.reduce((sum, a) => sum + getUnread(a.agent_id), 0);
}

// ---------------------------------------------------------------------------
// Collapse state persistence
// ---------------------------------------------------------------------------

/**
 * Read the current per-team collapsed map from localStorage.
 * Returns an empty object if nothing has been stored or the value is corrupt.
 */
export function getCollapsedState(): CollapsedState {
  try {
    const raw = localStorage.getItem(SIDEBAR_COLLAPSED_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (typeof parsed === 'object' && parsed !== null) return parsed as CollapsedState;
  } catch {
    // Corrupt storage — start fresh.
  }
  return {};
}

/**
 * Persist a single team's collapsed flag while leaving all other entries
 * intact. Writes only to the SIDEBAR_COLLAPSED_KEY key.
 */
export function setCollapsedState(teamId: string, collapsed: boolean): void {
  const current = getCollapsedState();
  current[teamId] = collapsed;
  try {
    localStorage.setItem(SIDEBAR_COLLAPSED_KEY, JSON.stringify(current));
  } catch {
    // Storage full or unavailable — silently ignore.
  }
}
