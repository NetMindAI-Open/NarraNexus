/**
 * Integration test for the sidebar's "recently chatted agent auto-pins to the
 * top" behavior — the wiring inside AgentList, not just the pure sort helper.
 *
 * Why this exists separately from agentGrouping.test.ts:
 * `sortAgentsByActivity` is unit-tested there. This file guards the LIVE
 * integration chain that the pure test can't see:
 *   rawAgents + agentSessions  →  useMemo(sortAgentsByActivity)  →  DOM order
 * i.e. it fails the day someone passes `rawAgents` (unsorted) back to
 * <AgentGroupSection> instead of `sortedAgents`, or drops the message
 * signature from the memo deps so a fresh local message no longer re-pins its
 * agent.
 *
 * The regression this locks down is the exact one seen on 2026-07-17: the
 * list stayed in creation order even though a just-chatted agent had a newer
 * timestamp.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// Guard against any accidental network — refreshAgents early-returns on an
// empty userId, but mocking api keeps the test hermetic if that ever changes.
vi.mock('@/lib/api', () => ({
  api: {
    getAgents: vi.fn().mockResolvedValue({ success: true, agents: [], count: 0 }),
    getTeams: vi.fn().mockResolvedValue({ success: true, teams: [] }),
  },
}));

import { AgentList } from '../AgentList';
import { useConfigStore, useChatStore, useTeamsStore } from '@/stores';
import type { AgentInfo } from '@/types/api';
import type { ChatMessage } from '@/types/messages';
import type { AgentChatState } from '@/stores/chatStore';

// ---------------------------------------------------------------------------
// Typed fixtures — no `as never`, so adding a required field to AgentInfo /
// AgentChatState surfaces here as a compile error instead of a silent gap.
// ---------------------------------------------------------------------------

/** A minimal committed assistant ChatMessage at a given epoch-ms. */
function assistantMsgAt(ms: number): ChatMessage {
  return { id: `m_${ms}`, role: 'assistant', content: 'hi', timestamp: ms };
}

/** A full, type-checked AgentChatState carrying only the given messages. */
function sessionWith(messages: ChatMessage[]): AgentChatState {
  return {
    messages,
    currentSteps: [],
    currentThinking: '',
    currentToolCalls: [],
    currentErrors: [],
    currentActionReason: null,
    currentAssistantMessage: '',
    isStreaming: false,
    history: [],
    currentEvents: [],
    currentRunId: null,
  };
}

/**
 * True when `first` appears before `second` in document order. Uses the two
 * name <span>s rendered per agent row.
 */
function isBefore(firstName: string, secondName: string): boolean {
  const a = screen.getByText(firstName);
  const b = screen.getByText(secondName);
  // DOCUMENT_POSITION_FOLLOWING on a→b is set when b comes AFTER a.
  return Boolean(
    a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING,
  );
}

// Two agents: Bravo has the newer server reply, Alpha the older one, so the
// initial (server/baseline) order is Bravo, then Alpha.
const ALPHA: AgentInfo = {
  agent_id: 'agent_alpha',
  name: 'Alpha',
  created_at: '2026-01-01T00:00:00Z',
  last_assistant_at: '2026-01-02T00:00:00Z',
};
const BRAVO: AgentInfo = {
  agent_id: 'agent_bravo',
  name: 'Bravo',
  created_at: '2026-01-01T00:00:00Z',
  last_assistant_at: '2026-07-01T00:00:00Z',
};

beforeEach(() => {
  localStorage.clear();
  // userId empty → refreshAgents() no-ops, so the seeded agents survive mount.
  useConfigStore.setState({ userId: '', agentId: '', agents: [ALPHA, BRAVO] });
  useChatStore.setState({ agentSessions: {} });
  // loaded=true so the mount effect doesn't kick off a teams refresh.
  useTeamsStore.setState({ teams: [], loaded: true });
});

const renderList = () =>
  render(
    <MemoryRouter>
      <AgentList collapsed={false} />
    </MemoryRouter>,
  );

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('AgentList — recent-conversation ordering', () => {
  it('orders agents by server last_assistant_at on first paint (newest on top)', () => {
    renderList();
    // Bravo replied more recently → above Alpha.
    expect(isBefore('Bravo', 'Alpha')).toBe(true);
  });

  it('re-pins an agent to the top when a fresh LOCAL message arrives', () => {
    renderList();
    // Baseline: Bravo above Alpha.
    expect(isBefore('Bravo', 'Alpha')).toBe(true);

    // Simulate chatting with Alpha: a local session message newer than
    // Bravo's server reply lands in agentSessions (not yet re-fetched).
    act(() => {
      useChatStore.setState({
        agentSessions: {
          [ALPHA.agent_id]: sessionWith([
            assistantMsgAt(Date.parse('2026-08-01T00:00:00Z')),
          ]),
        },
      });
    });

    // Alpha must now sit above Bravo — this is the whole point of blending
    // local session activity into the sort.
    expect(isBefore('Alpha', 'Bravo')).toBe(true);
  });

  it('renders in activity order, NOT the raw store order', () => {
    // Store order is [Alpha, Bravo]; if the list rendered rawAgents verbatim
    // Alpha would be first. Activity order puts Bravo first.
    renderList();
    expect(isBefore('Bravo', 'Alpha')).toBe(true);
  });
});
