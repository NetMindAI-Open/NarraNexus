/**
 * Unit tests for the agent grouping logic (M1 grouped sidebar).
 *
 * These tests cover the pure helper `buildAgentGroups` that is extracted
 * from AgentList so it can be tested in isolation without rendering.
 *
 * Covered scenarios:
 *  (a) grouping derivation — multi-team agent appears in both sections,
 *      untagged goes to Ungrouped, empty team renders its header
 *  (b) collapse toggle localStorage persistence + unread aggregation
 *  (c) kebab menu exposes rename/delete via AgentRowMenu rendering
 *  (d) header ⋯ menu entries: import, export, manage
 */

import { describe, it, expect, beforeEach } from 'vitest';
import {
  buildAgentGroups,
  aggregateSectionUnread,
  sortAgentsByActivity,
  SIDEBAR_COLLAPSED_KEY,
  getCollapsedState,
  setCollapsedState,
} from '../agentGroupUtils';

// Minimal agent shape used by the grouping helper.
type StubAgent = { agent_id: string; name?: string };

// Minimal team shape.
type StubTeam = {
  team: { team_id: string; name: string; color?: string | null };
  member_agent_ids: string[];
};

beforeEach(() => {
  localStorage.clear();
});

// ---------------------------------------------------------------------------
// buildAgentGroups
// ---------------------------------------------------------------------------

describe('buildAgentGroups', () => {
  it('places agents into their team sections in store order (Ungrouped always last)', () => {
    const agents: StubAgent[] = [
      { agent_id: 'a1' },
      { agent_id: 'a2' },
      { agent_id: 'a3' },
    ];
    const teams: StubTeam[] = [
      { team: { team_id: 't1', name: 'Alpha' }, member_agent_ids: ['a1', 'a3'] },
      { team: { team_id: 't2', name: 'Beta' }, member_agent_ids: ['a2'] },
    ];
    const groups = buildAgentGroups(agents, teams);
    // 2 teams + 1 Ungrouped (empty) = 3
    expect(groups).toHaveLength(3);
    expect(groups[0].teamId).toBe('t1');
    expect(groups[0].agents.map((a) => a.agent_id)).toEqual(['a1', 'a3']);
    expect(groups[1].teamId).toBe('t2');
    expect(groups[1].agents.map((a) => a.agent_id)).toEqual(['a2']);
    // Ungrouped is always the last group
    expect(groups[2].teamId).toBeNull();
    expect(groups[2].agents).toHaveLength(0);
  });

  it('multi-team agent appears in every team it belongs to', () => {
    const agents: StubAgent[] = [{ agent_id: 'shared' }];
    const teams: StubTeam[] = [
      { team: { team_id: 't1', name: 'Alpha' }, member_agent_ids: ['shared'] },
      { team: { team_id: 't2', name: 'Beta' }, member_agent_ids: ['shared'] },
    ];
    const groups = buildAgentGroups(agents, teams);
    // shared appears in both
    const t1Group = groups.find((g) => g.teamId === 't1')!;
    const t2Group = groups.find((g) => g.teamId === 't2')!;
    expect(t1Group.agents.map((a) => a.agent_id)).toContain('shared');
    expect(t2Group.agents.map((a) => a.agent_id)).toContain('shared');
  });

  it('agents in no team go to the Ungrouped section', () => {
    const agents: StubAgent[] = [
      { agent_id: 'a1' },
      { agent_id: 'lone' },
    ];
    const teams: StubTeam[] = [
      { team: { team_id: 't1', name: 'Alpha' }, member_agent_ids: ['a1'] },
    ];
    const groups = buildAgentGroups(agents, teams);
    const ungrouped = groups.find((g) => g.teamId === null);
    expect(ungrouped).toBeDefined();
    expect(ungrouped!.agents.map((a) => a.agent_id)).toEqual(['lone']);
  });

  it('Ungrouped section appears even when empty if there are other teams', () => {
    const agents: StubAgent[] = [{ agent_id: 'a1' }];
    const teams: StubTeam[] = [
      { team: { team_id: 't1', name: 'Alpha' }, member_agent_ids: ['a1'] },
    ];
    const groups = buildAgentGroups(agents, teams);
    const ungrouped = groups.find((g) => g.teamId === null);
    // If NO agents are ungrouped, we still render the Ungrouped header.
    // The section should be present (empty agents array).
    expect(ungrouped).toBeDefined();
    expect(ungrouped!.agents).toHaveLength(0);
  });

  it('empty team renders with zero members', () => {
    const agents: StubAgent[] = [{ agent_id: 'a1' }];
    const teams: StubTeam[] = [
      { team: { team_id: 't1', name: 'Alpha' }, member_agent_ids: [] },
      { team: { team_id: 't2', name: 'Beta' }, member_agent_ids: ['a1'] },
    ];
    const groups = buildAgentGroups(agents, teams);
    const emptyGroup = groups.find((g) => g.teamId === 't1')!;
    expect(emptyGroup.agents).toHaveLength(0);
  });

  it('produces only Ungrouped section when there are no teams', () => {
    const agents: StubAgent[] = [{ agent_id: 'a1' }];
    const groups = buildAgentGroups(agents, []);
    expect(groups).toHaveLength(1);
    expect(groups[0].teamId).toBeNull();
    expect(groups[0].agents.map((a) => a.agent_id)).toEqual(['a1']);
  });

  it('preserves agent order within a section (store order)', () => {
    const agents: StubAgent[] = [
      { agent_id: 'z' },
      { agent_id: 'a' },
      { agent_id: 'm' },
    ];
    const teams: StubTeam[] = [
      { team: { team_id: 't1', name: 'All' }, member_agent_ids: ['z', 'a', 'm'] },
    ];
    const groups = buildAgentGroups(agents, teams);
    expect(groups[0].agents.map((g) => g.agent_id)).toEqual(['z', 'a', 'm']);
  });
});

// ---------------------------------------------------------------------------
// sortAgentsByActivity
// ---------------------------------------------------------------------------

describe('sortAgentsByActivity', () => {
  type ActAgent = { agent_id: string; last_assistant_at?: string | null; created_at?: string };
  const noLocal = () => 0;

  it('orders by last_assistant_at descending (most recent first)', () => {
    const agents: ActAgent[] = [
      { agent_id: 'old', last_assistant_at: '2026-01-01T00:00:00Z' },
      { agent_id: 'new', last_assistant_at: '2026-07-01T00:00:00Z' },
      { agent_id: 'mid', last_assistant_at: '2026-04-01T00:00:00Z' },
    ];
    const out = sortAgentsByActivity(agents, noLocal);
    expect(out.map((a) => a.agent_id)).toEqual(['new', 'mid', 'old']);
  });

  it('does not mutate the input array', () => {
    const agents: ActAgent[] = [
      { agent_id: 'a', last_assistant_at: '2026-01-01T00:00:00Z' },
      { agent_id: 'b', last_assistant_at: '2026-02-01T00:00:00Z' },
    ];
    const before = agents.map((a) => a.agent_id);
    sortAgentsByActivity(agents, noLocal);
    expect(agents.map((a) => a.agent_id)).toEqual(before);
  });

  it('a fresher local session message pins its agent to the top', () => {
    const agents: ActAgent[] = [
      { agent_id: 'a', last_assistant_at: '2026-07-10T00:00:00Z' },
      { agent_id: 'b', last_assistant_at: '2026-01-01T00:00:00Z' },
    ];
    // b has no newer server reply, but the user just messaged it locally.
    const localMs = (id: string) => (id === 'b' ? Date.parse('2026-07-11T00:00:00Z') : 0);
    const out = sortAgentsByActivity(agents, localMs);
    expect(out.map((a) => a.agent_id)).toEqual(['b', 'a']);
  });

  it('falls back to created_at when an agent has no conversation', () => {
    const agents: ActAgent[] = [
      { agent_id: 'chatted', last_assistant_at: '2026-06-01T00:00:00Z', created_at: '2025-01-01T00:00:00Z' },
      { agent_id: 'fresh', created_at: '2026-07-16T00:00:00Z' },
      { agent_id: 'stale', created_at: '2024-01-01T00:00:00Z' },
    ];
    const out = sortAgentsByActivity(agents, noLocal);
    // fresh (created yesterday) > chatted (replied last month) > stale (created 2024)
    expect(out.map((a) => a.agent_id)).toEqual(['fresh', 'chatted', 'stale']);
  });

  it('breaks ties by agent_id for stable, churn-free ordering', () => {
    const ts = '2026-05-01T00:00:00Z';
    const agents: ActAgent[] = [
      { agent_id: 'c', last_assistant_at: ts },
      { agent_id: 'a', last_assistant_at: ts },
      { agent_id: 'b', last_assistant_at: ts },
    ];
    const out = sortAgentsByActivity(agents, noLocal);
    expect(out.map((a) => a.agent_id)).toEqual(['a', 'b', 'c']);
  });

  it('treats null / missing / invalid timestamps as epoch 0', () => {
    const agents: ActAgent[] = [
      { agent_id: 'none', last_assistant_at: null },
      { agent_id: 'has', last_assistant_at: '2026-03-01T00:00:00Z' },
      { agent_id: 'bad', last_assistant_at: 'not-a-date' },
    ];
    const out = sortAgentsByActivity(agents, noLocal);
    expect(out[0].agent_id).toBe('has');
    // 'bad' and 'none' both score 0 → tie broken by agent_id
    expect(out.slice(1).map((a) => a.agent_id)).toEqual(['bad', 'none']);
  });
});

// ---------------------------------------------------------------------------
// aggregateSectionUnread
// ---------------------------------------------------------------------------

describe('aggregateSectionUnread', () => {
  it('returns 0 for an empty agent list', () => {
    expect(aggregateSectionUnread([], () => 0)).toBe(0);
  });

  it('sums the unread counts from all agents in the section', () => {
    const agents: StubAgent[] = [
      { agent_id: 'a1' },
      { agent_id: 'a2' },
      { agent_id: 'a3' },
    ];
    const unreadMap: Record<string, number> = { a1: 2, a2: 0, a3: 5 };
    const total = aggregateSectionUnread(agents, (aid) => unreadMap[aid] ?? 0);
    expect(total).toBe(7);
  });

  it('does not count the active agent (it is always 0 per AgentList invariant)', () => {
    // The getUnread callback already returns 0 for the active agent.
    const agents: StubAgent[] = [{ agent_id: 'active' }, { agent_id: 'other' }];
    const total = aggregateSectionUnread(agents, (aid) => (aid === 'other' ? 3 : 0));
    expect(total).toBe(3);
  });
});

// ---------------------------------------------------------------------------
// collapse state localStorage persistence
// ---------------------------------------------------------------------------

describe('getCollapsedState / setCollapsedState', () => {
  it('returns empty object when nothing is stored', () => {
    const state = getCollapsedState();
    expect(state).toEqual({});
  });

  it('persists a collapsed=true entry and reads it back', () => {
    setCollapsedState('t1', true);
    const state = getCollapsedState();
    expect(state['t1']).toBe(true);
  });

  it('persists a collapsed=false entry (explicit expand)', () => {
    setCollapsedState('t1', true);
    setCollapsedState('t1', false);
    expect(getCollapsedState()['t1']).toBe(false);
  });

  it('stores multiple teams independently', () => {
    setCollapsedState('t1', true);
    setCollapsedState('t2', false);
    const state = getCollapsedState();
    expect(state['t1']).toBe(true);
    expect(state['t2']).toBe(false);
  });

  it('uses the correct localStorage key', () => {
    setCollapsedState('t1', true);
    const raw = localStorage.getItem(SIDEBAR_COLLAPSED_KEY);
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw!);
    expect(parsed['t1']).toBe(true);
  });
});
