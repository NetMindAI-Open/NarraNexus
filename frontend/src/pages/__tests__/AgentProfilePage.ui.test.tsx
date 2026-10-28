/**
 * @file_name: AgentProfilePage.ui.test.tsx
 * @author: NexusAgent
 * @date: 2026-08-24
 * @description: Information-architecture coverage for the Agent profile page.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, test, vi } from 'vitest';

const { configState, dashboardState, preloadState, chatState } = vi.hoisted(() => ({
  configState: {
    agentId: '',
    userId: 'owner-1',
    displayName: 'Owner',
    agents: [{
      agent_id: 'agent-1',
      name: 'Research Agent',
      description: 'Coordinates research work.',
      created_by: 'owner-1',
      agent_framework: 'codex_cli',
      model: 'gpt-5.5',
      bound_channels: [{ channel: 'lark', active: true }],
    }],
    setAgentId: vi.fn(),
    refreshAgents: vi.fn(),
  },
  dashboardState: {
    agents: [{
      agent_id: 'agent-1',
      name: 'Research Agent',
      description: 'Coordinates research work.',
      is_public: false,
      owned_by_viewer: true,
      status: { kind: 'idle', last_activity_at: '2026-08-24T12:00:00Z', started_at: null },
      running_count: 0,
      action_line: null,
      verb_line: 'Idle',
      sessions: [],
      running_jobs: [],
      pending_jobs: [],
      enhanced: {},
      queue: { running: 0, active: 0, pending: 0, blocked: 0, paused: 0, failed: 0, total: 0 },
      recent_events: [],
      metrics_today: { runs_ok: 1, errors: 0, avg_duration_ms: 8000, avg_duration_trend: 'flat', token_cost_cents: 0 },
      attention_banners: [],
      health: 'healthy_idle',
      stale_instances: [],
    }],
    onFetchSuccess: vi.fn(),
  },
  preloadState: {
    jobs: [],
    agentInboxRooms: [],
    agentInboxUnreadCount: 0,
  },
  chatState: { setActiveAgent: vi.fn(), clearAgent: vi.fn(), requestHistoryRefresh: vi.fn() },
}));

const teamsState = {
  teams: [{
    team: {
      team_id: 'team-1',
      owner_user_id: 'owner-1',
      name: 'Research Team',
      description: 'Coordinates the research workflow.',
      source: 'local',
    },
    member_agent_ids: ['agent-1'],
  }],
  refresh: vi.fn(),
};

const copy: Record<string, string> = {
  'pages.agentProfile.breadcrumb': 'Agents',
  'pages.agentProfile.backToChat': 'Chat',
  'pages.agentProfile.overview': 'Overview',
  'pages.agentProfile.capabilities': 'Capabilities',
  'pages.agentProfile.settings': 'Settings',
  'pages.agentProfile.general': 'General',
  'pages.agentProfile.currentTask': 'Current task',
  'pages.agentProfile.jobs': 'Jobs',
  'pages.agentProfile.inbox': 'Inbox',
  'pages.agentProfile.awareness': 'Awareness',
  'pages.agentProfile.network': 'Network',
  'pages.agentProfile.memory': 'Memory',
  'pages.agentProfile.skills': 'Skills',
  'pages.agentProfile.mcp': 'MCP',
  'pages.agentProfile.channels': 'Channels',
  'pages.agentProfile.modelFramework': 'Model & Framework',
  'pages.agentProfile.capabilitySwitches': 'Capability switches',
  'pages.agentProfile.configure': 'Configure',
  'pages.agentProfile.name': 'Name',
  'pages.agentProfile.description': 'Description',
  'pages.agentProfile.saveGeneral': 'Save changes',
  'pages.agentProfile.chat': 'Chat',
  'pages.agentProfile.idle': 'No task is currently running.',
  'pages.agentProfile.jobsCount': '0 jobs',
  'pages.agentProfile.inboxCount': '0 conversations',
  'pages.agentProfile.framework': 'Framework',
  'pages.agentProfile.model': 'Model',
  'layout.agentRowMenu.options': 'Agent options',
  'layout.agentRowMenu.clearData': 'Clear data',
  'layout.agentRowMenu.delete': 'Delete',
  'layout.agentList.deleteAgentTitle': 'Delete agent',
  'layout.agentList.deleteAction': 'Delete it',
  'layout.clearAgentData.title': 'Clear data',
  'layout.clearAgentData.subtitle': 'Choose what to clear.',
  'layout.clearAgentData.optConversations': 'Delete chat history',
  'layout.clearAgentData.optMemory': 'Delete memory',
  'layout.clearAgentData.cancel': 'Cancel',
  'layout.clearAgentData.confirm': 'Clear',
  'pages.dashboard.membersCount': '1 agent',
  'pages.dashboard.teamProfileNoDescription': 'No team description.',
};

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: { count?: number }) => {
      if (key === 'pages.agentProfile.jobsCount') return `${params?.count ?? 0} jobs`;
      if (key === 'pages.agentProfile.inboxCount') return `${params?.count ?? 0} conversations`;
      return copy[key] ?? key;
    },
    i18n: { language: 'en' },
  }),
}));
vi.mock('@/stores', () => ({
  useConfigStore: () => configState,
  useDashboardStore: (selector: (state: typeof dashboardState) => unknown) => selector(dashboardState),
  usePreloadStore: () => preloadState,
  useChatStore: () => chatState,
  useTeamsStore: () => teamsState,
}));
vi.mock('@/stores/dashboardStore', () => ({
  useDashboardStore: (selector: (state: typeof dashboardState) => unknown) => selector(dashboardState),
}));
vi.mock('@/lib/api', () => ({
  api: {
    getDashboardStatus: vi.fn().mockResolvedValue({ success: true, agents: dashboardState.agents }),
    clearHistory: vi.fn().mockResolvedValue({ success: true }),
    deleteAgent: vi.fn().mockResolvedValue({ success: true }),
    getAgentCapabilities: vi.fn().mockResolvedValue({ success: true, data: { agent_id: 'agent-1', capabilities: [], budget: { baseline_tokens: 0, enabled_tokens: 0, ratio: 0, over_budget: false } } }),
  },
}));
vi.mock('@/components/bookmarks/BookmarkPanelHost', () => ({
  BookmarkPanelHost: ({ tab }: { tab: string }) => <div data-testid="capability-panel">{tab}</div>,
}));
vi.mock('@/components/jobs/JobsPanel', () => ({ JobsPanel: () => <div>Jobs panel</div> }));
vi.mock('@/components/inbox/AgentInboxPanel', () => ({ AgentInboxPanel: () => <div>Inbox panel</div> }));
vi.mock('@/components/chat/AgentLlmConfigPanel', () => ({ AgentLlmConfigPanel: () => null }));
vi.mock('@/components/agents/AgentOverviewCard', () => ({
  AgentOverviewCard: (props: Record<string, unknown>) => (
    <div data-testid="agent-overview-card">
      {String(props.frameworkLabel)}|{String(props.modelLabel)}|{String(props.taskLabel)}|jobs:{String(props.jobsCount)}|inbox:{String(props.inboxCount)}
    </div>
  ),
}));
vi.mock('@/components/ui/tooltip', () => ({
  TooltipProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: React.ReactNode }) => (
    <div role="tooltip">{children}</div>
  ),
}));

import AgentProfilePage from '../AgentProfilePage';
import { api } from '@/lib/api';

describe('AgentProfilePage', () => {
  test('groups work in Overview and agent configuration in Capabilities', () => {
    render(
      <MemoryRouter initialEntries={['/app/agents/agent-1']}>
        <Routes>
          <Route path="/app/agents/:agentId" element={<AgentProfilePage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByRole('heading', { name: 'Research Agent' })).toBeTruthy();
    expect(screen.getByTestId('profile-team').querySelector('svg')).toBeTruthy();
    const teamAvatar = screen.getByTestId('team-avatar-trigger-team-1').querySelector('[data-nm="group-avatar"]');
    expect(Array.from(teamAvatar?.querySelectorAll('circle') ?? []).map((circle) => circle.getAttribute('stroke')))
      .toEqual(['var(--color-carbon)', 'var(--color-silicon)']);
    expect(screen.getByText('Coordinates the research workflow.')).toBeTruthy();
    const overviewCard = screen.getByTestId('agent-overview-card');
    expect(overviewCard.textContent).toContain('Codex');
    expect(overviewCard.textContent).toContain('gpt-5.5');
    expect(overviewCard.textContent).toContain('Idle');
    expect(screen.getByText('Jobs panel')).toBeTruthy();
    expect(screen.getByText('Inbox panel')).toBeTruthy();

    fireEvent.click(screen.getByRole('tab', { name: 'Capabilities' }));

    for (const label of ['Network', 'Memory', 'Skills', 'MCP', 'Channels']) {
      expect(screen.getByRole('button', { name: label })).toBeTruthy();
    }
    expect(screen.queryByRole('button', { name: 'Awareness' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Model & Framework' })).toBeNull();
    expect(screen.getByTestId('capability-panel').textContent).toBe('social');

    fireEvent.click(screen.getByRole('tab', { name: 'Settings' }));
    expect(screen.getByRole('button', { name: 'General' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Awareness' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Model & Framework' })).toBeTruthy();
    expect(screen.getByLabelText('Name')).toHaveValue('Research Agent');
    expect(screen.getByLabelText('Description')).toHaveValue('Coordinates research work.');

    fireEvent.click(screen.getByRole('button', { name: 'Awareness' }));
    expect(screen.getByTestId('capability-panel').textContent).toBe('awareness');

    // Settings → capability switches (batch 5c): its own section, not the top Capabilities tab.
    fireEvent.click(screen.getByRole('button', { name: 'Capability switches' }));
    expect(screen.getByTestId('agent-capabilities')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Model & Framework' }));
    expect(screen.getByTestId('profile-framework-config').querySelectorAll('svg, img')).toHaveLength(2);
    expect(screen.getByTestId('profile-model-config').querySelectorAll('svg, img')).toHaveLength(2);
    expect(screen.getAllByText('Codex CLI').length).toBeGreaterThan(0);
    expect(screen.getAllByText('gpt-5.5').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Configure' })).toBeTruthy();
  });

  test('the header kebab offers Clear data above Delete', () => {
    render(
      <MemoryRouter initialEntries={['/app/agents/agent-1']}>
        <Routes>
          <Route path="/app/agents/:agentId" element={<AgentProfilePage />} />
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Agent options' }));
    const entries = screen.getAllByRole('button')
      .map((node) => node.textContent)
      .filter((label) => label === 'Clear data' || label === 'Delete');
    // Order matters: clearing is the lesser blast radius, so it sits first.
    expect(entries).toEqual(['Clear data', 'Delete']);

    fireEvent.click(screen.getByRole('button', { name: 'Clear data' }));
    expect(screen.getByText('Delete chat history')).toBeTruthy();
  });

  test('Overview carries the activity band, ordered after the summary card', () => {
    render(
      <MemoryRouter initialEntries={['/app/agents/agent-1']}>
        <Routes>
          <Route path="/app/agents/:agentId" element={<AgentProfilePage />} />
        </Routes>
      </MemoryRouter>,
    );

    const summary = screen.getByTestId('agent-overview-card');
    const activity = screen.getByTestId('agent-activity-card');
    // Summary before activity: the static "what is this agent" reads before
    // the temporal "how has it been doing".
    expect(summary.compareDocumentPosition(activity) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
  });

  test('someone else\'s public agent offers no kebab and no Settings tab', () => {
    // Regression: the sidebar kebab this page replaced hid Clear data / Delete
    // behind an owner check; the first cut of this page rendered them for any
    // agent in the list, and the backend then answered 404 — which reads as
    // "the platform is broken", not "not yours".
    const own = configState.agents[0];
    configState.agents = [{ ...own, created_by: 'someone-else' }];
    try {
      render(
        <MemoryRouter initialEntries={['/app/agents/agent-1']}>
          <Routes>
            <Route path="/app/agents/:agentId" element={<AgentProfilePage />} />
          </Routes>
        </MemoryRouter>,
      );
      expect(screen.queryByLabelText('Agent options')).toBeNull();
      expect(screen.queryByRole('tab', { name: 'Settings' })).toBeNull();
      expect(screen.getByRole('tab', { name: 'Overview' })).toBeTruthy();
      expect(screen.getByRole('tab', { name: 'Capabilities' })).toBeTruthy();
    } finally {
      configState.agents = [own];
    }
  });

  test('the owner keeps the kebab and the Settings tab', () => {
    render(
      <MemoryRouter initialEntries={['/app/agents/agent-1']}>
        <Routes>
          <Route path="/app/agents/:agentId" element={<AgentProfilePage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByLabelText('Agent options')).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Settings' })).toBeTruthy();
  });

  test('a public agent gets neither banners nor the activity band', () => {
    const owned = dashboardState.agents[0];
    dashboardState.agents = [{ ...owned, owned_by_viewer: false }] as typeof dashboardState.agents;
    try {
      render(
        <MemoryRouter initialEntries={['/app/agents/agent-1']}>
          <Routes>
            <Route path="/app/agents/:agentId" element={<AgentProfilePage />} />
          </Routes>
        </MemoryRouter>,
      );
      // Every field these two read lives on OwnedAgentStatus; rendering them
      // for a foreign agent would mean reading someone else's private state.
      expect(screen.queryByTestId('agent-activity-card')).toBeNull();
      expect(screen.queryByTestId('banner-job_failed')).toBeNull();
    } finally {
      dashboardState.agents = [owned];
    }
  });

  test('Delete confirms, deletes through the shared agent actions and lands on the Dashboard', async () => {
    // The profile page and the sidebar row menu share useAgentActions; this
    // pins the profile's half of that contract after the extraction.
    render(
      <MemoryRouter initialEntries={['/app/agents/agent-1']}>
        <Routes>
          <Route path="/app/agents/:agentId" element={<AgentProfilePage />} />
          <Route path="/app/dashboard" element={<div>Dashboard landed</div>} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Agent options' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(await screen.findByText('Delete agent')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Delete it' }));
    expect(await screen.findByText('Dashboard landed')).toBeTruthy();
    expect(api.deleteAgent).toHaveBeenCalledWith('agent-1');
    expect(chatState.clearAgent).toHaveBeenCalledWith('agent-1');
  });
});
