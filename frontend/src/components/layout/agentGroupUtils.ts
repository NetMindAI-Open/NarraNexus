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

/** Agent shape required by activity-based sorting. */
export interface SortableAgent extends GroupableAgent {
  /** ISO time of the last persisted assistant reply (server-provided). */
  last_assistant_at?: string | null;
  /** ISO agent creation time — floor used when there's no conversation yet. */
  created_at?: string;
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
// sortAgentsByActivity
// ---------------------------------------------------------------------------

/** Parse an ISO timestamp to epoch-ms; 0 for empty/invalid input. */
function isoToMs(iso: string | null | undefined): number {
  if (!iso) return 0;
  const ms = new Date(iso).getTime();
  return Number.isNaN(ms) ? 0 : ms;
}

/**
 * Sort agents so the most-recently-active conversation floats to the top —
 * "recently chatted agent auto-pins to the top".
 *
 * An agent's activity time is the NEWEST of three signals:
 *  - `last_assistant_at`: the server's last persisted assistant reply
 *  - `localActivityMs(agent_id)`: the freshest local session message
 *    (user- or agent-sent, possibly not yet persisted). This is what makes
 *    an agent jump to the top the instant you talk to it, before the next
 *    /api/auth/agents refresh.
 *  - `created_at`: a floor so a brand-new, never-chatted agent still orders
 *    sensibly (by creation recency) instead of collapsing to epoch 0.
 *
 * Pure and stable: returns a NEW array and never mutates the input; ties are
 * broken by agent_id so equal-timestamp order doesn't churn between renders.
 */
export function sortAgentsByActivity<A extends SortableAgent>(
  agents: A[],
  localActivityMs: (agentId: string) => number,
): A[] {
  const score = (a: A): number =>
    Math.max(
      isoToMs(a.last_assistant_at),
      localActivityMs(a.agent_id) || 0,
      isoToMs(a.created_at),
    );
  // Decorate-sort-undecorate: score() calls localActivityMs(), which scans an
  // agent's whole message list, so it must run exactly ONCE per agent — not
  // the ~2·n·log₂n times a comparator that calls score(a)/score(b) inline
  // would. This sort sits on the streaming path (AgentList re-runs it as chat
  // state changes), and message lists grow without bound in long sessions, so
  // the O(n·m) → single-pass difference is what keeps it off the hot path.
  const scored = agents.map((a) => ({ agent: a, s: score(a) }));
  scored.sort((x, y) => {
    if (x.s !== y.s) return y.s - x.s;
    return x.agent.agent_id < y.agent.agent_id
      ? -1
      : x.agent.agent_id > y.agent.agent_id
        ? 1
        : 0;
  });
  return scored.map((x) => x.agent);
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
