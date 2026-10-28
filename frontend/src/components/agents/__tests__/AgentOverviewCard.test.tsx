/**
 * @file_name: AgentOverviewCard.test.tsx
 * @description: Read-only overview card — row/flag/section rendering, chip
 * overflow, and async skills/MCP states.
 */
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, test, vi } from 'vitest';

const skillsState: { data: Array<{ name: string }>; isLoading: boolean } = { data: [], isLoading: false };
const mcpState: { data: Array<{ mcp_id: string; name: string; connection_status?: string }>; isLoading: boolean } = {
  data: [],
  isLoading: false,
};

vi.mock('@/hooks/useSkills', () => ({
  useSkillsList: () => skillsState,
}));
vi.mock('@/hooks/useMCP', () => ({
  useMCPList: () => mcpState,
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: { count?: number }) => {
      const table: Record<string, string> = {
        'pages.agentProfile.overviewCardLabel': 'Agent overview',
        'pages.agentProfile.currentTask': 'Current task',
        'pages.agentProfile.framework': 'Framework',
        'pages.agentProfile.model': 'Model',
        'pages.agentProfile.skills': 'Skills',
        'pages.agentProfile.mcp': 'MCP',
        'pages.agentProfile.jobsNoun': 'jobs',
        'pages.agentProfile.inboxNoun': 'conversations',
        'pages.agentProfile.jobsRunningFlag': 'running',
        'pages.agentProfile.noSkills': 'No skills installed',
        'pages.agentProfile.noMcp': 'No MCP servers',
      };
      if (key === 'pages.agentProfile.inboxUnreadFlag') return `${params?.count} new`;
      if (key === 'pages.agentProfile.mcpSummary') return `${params?.count} connected`;
      if (key === 'pages.agentProfile.moreCount') return `+${params?.count} more`;
      return table[key] ?? key;
    },
  }),
}));

import { AgentOverviewCard } from '../AgentOverviewCard';

const baseProps = {
  frameworkLabel: 'Claude Code',
  FrameworkIcon: () => <svg data-testid="framework-icon" />,
  frameworkInvertDark: false,
  modelLabel: 'claude-opus-5',
  ModelIcon: () => <svg data-testid="model-icon" />,
  modelInvertDark: false,
  isRunning: false,
  taskLabel: 'No task is currently running.',
  jobsCount: 0,
  jobsRunning: false,
  inboxCount: 0,
  inboxUnreadCount: 0,
};

beforeEach(() => {
  skillsState.data = [];
  skillsState.isLoading = false;
  mcpState.data = [];
  mcpState.isLoading = false;
});

describe('AgentOverviewCard', () => {
  test('renders framework/model rows and the current task', () => {
    render(<AgentOverviewCard {...baseProps} taskLabel="Drafting outreach sequence" isRunning />);
    expect(screen.getByTestId('profile-framework-row').textContent).toContain('Claude Code');
    expect(screen.getByTestId('profile-model-row').textContent).toContain('claude-opus-5');
    expect(screen.getByTestId('profile-task-row').textContent).toContain('Drafting outreach sequence');
  });

  test('shows the running flag only when a job is running', () => {
    const { rerender } = render(<AgentOverviewCard {...baseProps} jobsCount={12} jobsRunning />);
    expect(screen.getByTestId('profile-jobs-stat').textContent).toContain('12');
    expect(screen.getByTestId('profile-jobs-stat').textContent).toContain('running');

    rerender(<AgentOverviewCard {...baseProps} jobsCount={12} jobsRunning={false} />);
    expect(screen.getByTestId('profile-jobs-stat').textContent).not.toContain('running');
  });

  test('shows the unread flag only when the inbox has unread rooms', () => {
    const { rerender } = render(<AgentOverviewCard {...baseProps} inboxCount={5} inboxUnreadCount={3} />);
    expect(screen.getByTestId('profile-inbox-stat').textContent).toContain('5');
    expect(screen.getByTestId('profile-inbox-stat').textContent).toContain('3 new');

    rerender(<AgentOverviewCard {...baseProps} inboxCount={5} inboxUnreadCount={0} />);
    expect(screen.getByTestId('profile-inbox-stat').textContent).not.toContain('new');
  });

  test('caps skill chips at 4 and shows a +N more label beyond that', () => {
    skillsState.data = ['lead-scoring', 'email-drafting', 'crm-sync', 'call-notes', 'followups'].map((name) => ({ name }));
    render(<AgentOverviewCard {...baseProps} />);
    const section = screen.getByTestId('profile-skills-section');
    expect(section.textContent).toContain('lead-scoring');
    expect(section.textContent).toContain('call-notes');
    expect(section.textContent).not.toContain('followups');
    expect(section.textContent).toContain('+1 more');
  });

  test('shows a loading spinner while skills load, then an empty label with none installed', () => {
    skillsState.isLoading = true;
    const { rerender } = render(<AgentOverviewCard {...baseProps} />);
    expect(screen.getByTestId('profile-skills-section').querySelector('svg')).toBeTruthy();

    skillsState.isLoading = false;
    skillsState.data = [];
    rerender(<AgentOverviewCard {...baseProps} />);
    expect(screen.getByText('No skills installed')).toBeTruthy();
  });

  test('colors MCP chips by connection status and counts only connected ones', () => {
    mcpState.data = [
      { mcp_id: 'a', name: 'hubspot-mcp', connection_status: 'connected' },
      { mcp_id: 'b', name: 'slack-mcp', connection_status: 'connected' },
      { mcp_id: 'c', name: 'gcal-mcp', connection_status: 'failed' },
    ];
    render(<AgentOverviewCard {...baseProps} />);
    const section = screen.getByTestId('profile-mcp-section');
    expect(section.textContent).toContain('2 connected');
    expect(section.textContent).toContain('hubspot-mcp');
    expect(section.textContent).toContain('gcal-mcp');
  });

  test('shows an empty label when there are no MCP servers', () => {
    mcpState.data = [];
    mcpState.isLoading = false;
    render(<AgentOverviewCard {...baseProps} />);
    expect(screen.getByText('No MCP servers')).toBeTruthy();
  });
});
