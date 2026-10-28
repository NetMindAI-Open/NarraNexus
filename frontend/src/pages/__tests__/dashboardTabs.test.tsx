/**
 * @file_name: dashboardTabs.test.tsx
 * @description: Req #1 — the Dashboard's agent/team management is a left-rail
 * master–detail with THREE tabs (Manage Agents / Team Management / Export),
 * each reachable, with Create-Agent (agents) and Create-Team (teams) buttons,
 * and ?tab=export landing on the embedded export wizard. Reverting the tabbed
 * shell or a create button turns one of these red.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const { navigate, createAgent, useDashboardStore } = vi.hoisted(() => {
  const dashState = {
    agents: [],
    error: null,
    setVisibility: () => {},
    setTauriFocused: () => {},
    onFetchSuccess: () => {},
    onFetchError: () => {},
    onRateLimited: () => {},
    computeInterval: () => Infinity,
    computeRunningCount: () => 0,
    lastTrayCount: 0,
  };
  const store = ((sel: (s: unknown) => unknown) => sel(dashState)) as unknown as {
    (sel: (s: unknown) => unknown): unknown;
    getState: () => typeof dashState;
    setState: (p: unknown) => void;
  };
  store.getState = () => dashState;
  store.setState = () => {};
  return { navigate: vi.fn(), createAgent: vi.fn(), useDashboardStore: store };
});

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});
vi.mock('@/stores/dashboardStore', () => ({ useDashboardStore }));

vi.mock('@/stores', () => ({
  useConfigStore: () => ({
    agents: [],
    refreshAgents: vi.fn().mockResolvedValue(undefined),
    userId: 'u_1',
    displayName: 'Tester',
    setAgentId: vi.fn(),
  }),
  useTeamsStore: () => ({
    teams: [],
    refresh: vi.fn().mockResolvedValue(undefined),
    addMember: vi.fn(),
    removeMember: vi.fn(),
    updateTeam: vi.fn(),
    deleteTeam: vi.fn(),
  }),
  useChatStore: () => ({
    setActiveAgent: vi.fn(),
    requestHistoryRefresh: vi.fn(),
    requestWorkspaceRefresh: vi.fn(),
  }),
}));
vi.mock('@/hooks', () => ({ useCreateAgent: () => ({ creating: false, createAgent }) }));
vi.mock('@/lib/api', () => ({
  api: {
    getDashboardStatus: vi.fn().mockResolvedValue({ success: true, agents: [] }),
    deleteAgent: vi.fn(),

    getAgentLlmConfig: vi.fn().mockResolvedValue({ success: true, data: { slots: {} } }),
  },
}));
vi.mock('@/lib/tauri', () => ({
  setTrayBadge: vi.fn().mockResolvedValue(undefined),
  listenTauri: vi.fn().mockResolvedValue(() => {}),
}));
// Same specifier the source imports, so the mock doesn't lean on alias-resolution
// coincidence (the lazy import in DashboardPage is '@/pages/BundleExportPage').
vi.mock('@/pages/BundleExportPage', () => ({ default: () => <div>export-wizard-stub</div> }));

import DashboardPage from '../DashboardPage';
import { api } from '@/lib/api';
import {
  MASTER_DETAIL_ROW_CLASS,
  MASTER_NAV_CLASS,
  MASTER_NAV_ITEM_CLASS,
} from '@/lib/masterDetailNav';

const renderAt = (path: string) =>
  render(<MemoryRouter initialEntries={[path]}><DashboardPage /></MemoryRouter>);

beforeEach(() => {
  navigate.mockClear();
  createAgent.mockClear();
  (api.getDashboardStatus as ReturnType<typeof vi.fn>).mockClear();
});

describe('Dashboard left-rail tabs (#1)', () => {
  it('renders three tabs, defaults to agents with a Create Agent button', () => {
    renderAt('/app/dashboard');
    expect(screen.getByRole('button', { name: /manage agents/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /team management/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^export$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /create agent/i })).toBeInTheDocument();
  });

  it('the left rail collapses to a horizontal strip below md (GitHub #130)', () => {
    // The rail's responsive classes live in one shared definition
    // (lib/masterDetailNav) so this page and its sibling cannot drift; the
    // breakpoint pin itself is asserted in masterDetailNav.test.ts.
    renderAt('/app/dashboard');
    const nav = screen.getByRole('navigation');
    expect(nav.className).toBe(MASTER_NAV_CLASS);
    expect(nav.parentElement?.className).toBe(MASTER_DETAIL_ROW_CLASS);
    const item = nav.querySelector('button');
    expect(item?.className.split(/\s+/)).toEqual(
      expect.arrayContaining(MASTER_NAV_ITEM_CLASS.split(' ')),
    );
  });

  it('the Create Agent button invokes useCreateAgent().createAgent', () => {
    renderAt('/app/dashboard');
    fireEvent.click(screen.getByRole('button', { name: /create agent/i }));
    expect(createAgent).toHaveBeenCalled();
  });

  it('teams tab shows a Create Team button that opens the create page', () => {
    renderAt('/app/dashboard');
    fireEvent.click(screen.getByRole('button', { name: /team management/i }));
    fireEvent.click(screen.getByRole('button', { name: /create team/i }));
    expect(navigate).toHaveBeenCalledWith('/app/teams/new');
  });

  it('?tab=export lands on the embedded export wizard (lazy-loaded)', async () => {
    renderAt('/app/dashboard?tab=export');
    expect(await screen.findByText('export-wizard-stub')).toBeInTheDocument();
  });

  it('the Export tab pauses the status polling (no /dashboard/status fetch)', () => {
    renderAt('/app/dashboard?tab=export');
    expect(api.getDashboardStatus).not.toHaveBeenCalled();
  });

  it('a normal tab DOES poll (guards that the pause is export-only)', () => {
    renderAt('/app/dashboard');
    expect(api.getDashboardStatus).toHaveBeenCalled();
  });
});
