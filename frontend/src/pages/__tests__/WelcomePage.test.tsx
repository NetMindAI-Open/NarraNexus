/**
 * @file_name: WelcomePage.test.tsx
 * @date: 2026-08-27
 * @description: The first-run flow's contracts — the ones a broken build would
 * inflict on every new user:
 *   - the flow only shows the steps that apply (cloud never gets import);
 *   - the last step's CTA selects the guide agent and lands in chat;
 *   - `landing_completed` is written exactly once, on ANY exit (finish, skip,
 *     or nothing-to-do), so the flow can never replay;
 *   - an empty step list redirects instead of rendering an empty shell.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { WelcomePage } from '../WelcomePage';

const mockNavigate = vi.fn();
// ?next= is how ProtectedRoute hands the flow the URL the user was heading to.
let searchParams = new URLSearchParams();
vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
  useSearchParams: () => [searchParams] as const,
}));

const runtimeState = { mode: 'local' as 'local' | 'cloud-web' };
const configState = { userId: 'u_new', setAgentId: vi.fn(), agentId: '' };
const chatState = { setActiveAgent: vi.fn() };
vi.mock('@/stores', () => ({
  useConfigStore: Object.assign(
    (selector?: (s: typeof configState) => unknown) =>
      selector ? selector(configState) : configState,
    { getState: () => configState },
  ),
  useRuntimeStore: (selector: (s: typeof runtimeState) => unknown) => selector(runtimeState),
  useChatStore: Object.assign(() => chatState, { getState: () => chatState }),
}));

const mockGetProviders = vi.fn();
const mockGetAgents = vi.fn();
const mockMigrateDetect = vi.fn();
const mockMarkStep = vi.fn();
vi.mock('@/lib/api', () => ({
  api: {
    getProviders: (...a: unknown[]) => mockGetProviders(...a),
    getAgents: (...a: unknown[]) => mockGetAgents(...a),
    migrateDetect: (...a: unknown[]) => mockMigrateDetect(...a),
    markOnboardingStep: (...a: unknown[]) => mockMarkStep(...a),
  },
}));

// The model step embeds the real OneKeyOnboard (own API surface, own confirm
// dialog); this flow test only cares that the step renders and can be skipped.
vi.mock('@/components/settings/OneKeyOnboard', () => ({
  OneKeyOnboard: () => <div data-testid="one-key" />,
}));

vi.mock('@/hooks', () => ({
  useAgentImported: () => vi.fn(),
  useAgentImport: () => {
    throw new Error('the import step must not mount in these cases');
  },
}));

const GUIDE = {
  agent_id: 'agt_guide',
  name: 'Wren',
  bootstrap_active: true,
  bootstrap_greeting: 'I just woke up.\n\n---\n\n我刚醒过来。',
  bound_channels: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  runtimeState.mode = 'local';
  searchParams = new URLSearchParams();
  mockGetProviders.mockResolvedValue({ success: true, data: { providers: {} } });
  mockGetAgents.mockResolvedValue({ agents: [GUIDE] });
  mockMigrateDetect.mockResolvedValue({ detections: [] });
  mockMarkStep.mockResolvedValue({ success: true });
});

describe('WelcomePage', () => {
  it('shows model then agent when the machine has nothing to import', async () => {
    render(<WelcomePage />);
    // step 1: the model card
    await waitFor(() => expect(screen.getByTestId('one-key')).toBeInTheDocument());
    // the rail lists exactly the two applicable steps, agent named.
    // ("wire a model" appears twice by design: rail entry + page heading.)
    expect(screen.getAllByText(/wire a model/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/meet wren/i)).toBeInTheDocument();
    expect(screen.queryByText(/bring your agents over/i)).not.toBeInTheDocument();
    expect(mockMigrateDetect).toHaveBeenCalled();
  });

  it('never probes the filesystem on cloud, and offers no import step', async () => {
    runtimeState.mode = 'cloud-web';
    render(<WelcomePage />);
    await waitFor(() => expect(screen.getByTestId('one-key')).toBeInTheDocument());
    expect(mockMigrateDetect).not.toHaveBeenCalled();
    expect(screen.queryByText(/bring your agents over/i)).not.toBeInTheDocument();
  });

  it('skips into the agent step, then lands in the guide agent chat', async () => {
    render(<WelcomePage />);
    await waitFor(() => expect(screen.getByTestId('one-key')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /skip for now/i }));
    await waitFor(() => expect(screen.getByText(/^Meet Wren\.$/)).toBeInTheDocument());
    // the greeting preview keeps only the English half of the bilingual text
    expect(screen.getByText('I just woke up.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /start using narranexus/i }));
    await waitFor(() =>
      expect(mockMarkStep).toHaveBeenCalledWith('u_new', 'landing_completed'),
    );
    expect(configState.setAgentId).toHaveBeenCalledWith('agt_guide');
    expect(chatState.setActiveAgent).toHaveBeenCalledWith('agt_guide');
    expect(mockNavigate).toHaveBeenCalledWith('/app/chat', { replace: true });
  });

  it('writes landing_completed once even when every step is skipped', async () => {
    render(<WelcomePage />);
    await waitFor(() => expect(screen.getByTestId('one-key')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /skip for now/i }));
    await waitFor(() => expect(screen.getByText(/^Meet Wren\.$/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /go straight to the app/i }));

    await waitFor(() => expect(mockNavigate).toHaveBeenCalled());
    expect(mockMarkStep).toHaveBeenCalledTimes(1);
    expect(mockMarkStep).toHaveBeenCalledWith('u_new', 'landing_completed');
  });

  it('hands the user back to the ?next= destination the gate captured', async () => {
    searchParams = new URLSearchParams('next=%2Fapp%2Fbundle%2Fimport');
    render(<WelcomePage />);
    await waitFor(() => expect(screen.getByTestId('one-key')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /skip for now/i }));
    await waitFor(() => expect(screen.getByText(/^Meet Wren\.$/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /go straight to the app/i }));

    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith('/app/bundle/import', { replace: true }),
    );
  });

  it('still ends in the agent chat when the user opens the agent, ?next= or not', async () => {
    searchParams = new URLSearchParams('next=%2Fapp%2Fdashboard');
    render(<WelcomePage />);
    await waitFor(() => expect(screen.getByTestId('one-key')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /skip for now/i }));
    await waitFor(() => expect(screen.getByText(/^Meet Wren\.$/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /start using narranexus/i }));

    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith('/app/chat', { replace: true }),
    );
  });

  it('still offers the model step when login already auto-registered providers', async () => {
    // Regression guard for the 2026-08-27 report ("why only two steps, where is
    // the provider one?"): login auto-registers NetMind cards, so a brand-new
    // account arrives WITH providers. The step must survive that.
    mockGetProviders.mockResolvedValue({
      success: true,
      data: { providers: { prov_a: { source: 'netmind' }, prov_b: { source: 'netmind' } } },
    });

    render(<WelcomePage />);
    await waitFor(() => expect(screen.getByTestId('one-key')).toBeInTheDocument());
    expect(screen.getAllByText(/wire a model/i).length).toBeGreaterThan(0);
    expect(mockNavigate).not.toHaveBeenCalled();
  });
});
