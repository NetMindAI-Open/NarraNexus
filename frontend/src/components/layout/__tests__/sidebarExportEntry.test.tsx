/**
 * @file_name: sidebarExportEntry.test.tsx
 * @description: Req #1 (导出融入智能体管理) — the sidebar "Export" nav row
 * deep-links into the Dashboard's Export tab, and the active-highlight is driven
 * by a PARSED `?tab=` (not a substring `includes`), so exactly one of the
 * Export / Manage-Agents rows lights up and `?tab=exportfoo` never false-matches.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const ACTIVE = 'bg-[var(--nm-row-active)]'; // NAV_ROW_ACTIVE token

const { navigate, loc } = vi.hoisted(() => ({
  navigate: vi.fn(),
  loc: { current: { pathname: '/app/chat', search: '' } as { pathname: string; search: string } },
}));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate, useLocation: () => loc.current };
});

const cfg = { userId: 'u1', displayName: 'U', logout: vi.fn(), netmindToken: null };
const uiState = { mobileNavOpen: false, sidebarCollapsed: false, toggleSidebar: vi.fn() };
vi.mock('@/stores', () => ({
  useConfigStore: (sel?: (s: unknown) => unknown) => (sel ? sel(cfg) : cfg),
  useChatStore: () => ({ clearAll: vi.fn() }),
  useRuntimeStore: () => ({ mode: 'local', features: { showSystemPage: false }, setMode: vi.fn(), setCloudApiUrl: vi.fn() }),
  usePreloadStore: (sel: (s: unknown) => unknown) => sel({ clearAll: vi.fn() }),
  useUIStore: (sel: (s: unknown) => unknown) => sel(uiState),
}));
vi.mock('@/hooks', () => ({
  useTheme: () => ({ isDark: false }),
  useCreateAgent: () => ({ createAgent: vi.fn(), creating: false }),
  useAgentImported: () => vi.fn(),
  useDismissOnOutside: () => ({ current: null }),
}));
vi.mock('@/hooks/useMediaQuery', () => ({ useIsMobile: () => false }));
vi.mock('@/components/ui', () => ({
  BetaBadge: () => null,
  ScrollArea: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  useConfirm: () => ({ confirm: vi.fn(), dialog: null }),
}));
vi.mock('@/components/ui/FeedbackDialog', () => ({ FeedbackDialog: () => null }));
vi.mock('@/components/nm', () => ({ RingAvatar: () => null, StatusDot: () => null }));
vi.mock('../AgentList', () => ({ AgentList: () => null }));
vi.mock('../CreateMenu', () => ({ CreateMenu: () => null }));
vi.mock('../ImportAgentModal', () => ({ ImportAgentModal: () => null }));

import { Sidebar } from '../Sidebar';

beforeEach(() => {
  navigate.mockClear();
  loc.current = { pathname: '/app/chat', search: '' };
});

describe('sidebar Export row (#1 融入)', () => {
  it('deep-links into the dashboard export tab', () => {
    render(<Sidebar />);
    fireEvent.click(screen.getByRole('button', { name: /^export$/i }));
    expect(navigate).toHaveBeenCalledWith('/app/dashboard?tab=export');
  });

  it('at ?tab=export exactly the Export row is active (Manage-Agents is not)', () => {
    loc.current = { pathname: '/app/dashboard', search: '?tab=export' };
    render(<Sidebar />);
    expect(screen.getByRole('button', { name: /^export$/i }).className).toContain(ACTIVE);
    expect(screen.getByRole('button', { name: /^dashboard$/i }).className).not.toContain(ACTIVE);
  });

  it('?tab=exportfoo does NOT false-match the Export row (parsed, not includes)', () => {
    loc.current = { pathname: '/app/dashboard', search: '?tab=exportfoo' };
    render(<Sidebar />);
    expect(screen.getByRole('button', { name: /^export$/i }).className).not.toContain(ACTIVE);
    // unknown tab → Manage-Agents (dashboard) row is the active one
    expect(screen.getByRole('button', { name: /^dashboard$/i }).className).toContain(ACTIVE);
  });
});
