/**
 * @file_name: AgentActivityCard.test.tsx
 * @author: NexusAgent
 * @date: 2026-08-27
 * @description: The activity band's own logic is the conditional secondary
 * row — Sparkline/MetricsRow always render, but the rule beneath them must
 * not appear as a bare strip when a quiet agent has neither live sessions
 * nor recent events. Both children self-hide, so only the wrapper can decide.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en' } }),
}));
vi.mock('@/components/dashboard/Sparkline', () => ({
  Sparkline: ({ agentId }: { agentId: string }) => <div data-testid="sparkline">{agentId}</div>,
}));
vi.mock('@/components/dashboard/MetricsRow', () => ({
  MetricsRow: () => <div data-testid="metrics-row" />,
}));
vi.mock('@/components/dashboard/SessionSection', () => ({
  SessionSection: () => <div data-testid="session-section" />,
}));
vi.mock('@/components/dashboard/RecentFeed', () => ({
  RecentFeed: () => <div data-testid="recent-feed" />,
}));

import { AgentActivityCard } from '../AgentActivityCard';
import type { OwnedAgentStatus } from '@/types';

function statusOf(overrides: Partial<OwnedAgentStatus> = {}): OwnedAgentStatus {
  return {
    agent_id: 'agent-1',
    name: 'Atlas',
    owned_by_viewer: true,
    status: { kind: 'idle', last_activity_at: null, started_at: null },
    health: 'healthy_idle',
    sessions: [],
    recent_events: [],
    metrics_today: {
      runs_ok: 0, errors: 0, avg_duration_ms: null,
      avg_duration_trend: 'unknown', token_cost_cents: null,
    },
    ...overrides,
  } as unknown as OwnedAgentStatus;
}

describe('AgentActivityCard', () => {
  test('always shows the 24h shape and today totals', () => {
    render(<AgentActivityCard agentId="agent-1" status={statusOf()} />);
    expect(screen.getByTestId('sparkline').textContent).toBe('agent-1');
    expect(screen.getByTestId('metrics-row')).toBeTruthy();
  });

  test('a quiet agent gets no secondary band at all', () => {
    render(<AgentActivityCard agentId="agent-1" status={statusOf()} />);
    expect(screen.queryByTestId('session-section')).toBeNull();
    expect(screen.queryByTestId('recent-feed')).toBeNull();
    // The rule + padding must go with them, or the card ends in a bare strip.
    expect(screen.queryByTestId('agent-activity-detail')).toBeNull();
  });

  test('either live sessions or recent events bring the secondary band back', () => {
    const { rerender } = render(
      <AgentActivityCard
        agentId="agent-1"
        status={statusOf({ sessions: [{ session_id: 's1' }] as never })}
      />,
    );
    expect(screen.getByTestId('session-section')).toBeTruthy();
    expect(screen.queryByTestId('recent-feed')).toBeNull();

    rerender(
      <AgentActivityCard
        agentId="agent-1"
        status={statusOf({ recent_events: [{ event_id: 'e1' }] as never })}
      />,
    );
    expect(screen.queryByTestId('session-section')).toBeNull();
    expect(screen.getByTestId('recent-feed')).toBeTruthy();
  });
});
