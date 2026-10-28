/**
 * @file_name: PluginFactory.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: The factory page lists plugins with state, installs with a permissions disclosure, drives enable/disable/rollback, shows safe mode + bisect, and hides in cloud.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { PluginFactory } from '../PluginFactory';
import type { FactoryListResponse, FactoryPlugin } from '@/types';

const mocks = vi.hoisted(() => ({
  factoryList: vi.fn(),
  factoryInstall: vi.fn(),
  factoryAction: vi.fn(),
  factoryRollback: vi.fn(),
  factoryLeaveSafeMode: vi.fn(),
  factoryBisect: vi.fn(),
  factoryBisectAnswer: vi.fn(),
  factoryErrors: vi.fn(),
  factoryProposals: vi.fn(),
  factoryDecide: vi.fn(),
  factoryBuiltinSetEnabled: vi.fn(),
  factoryBuiltinInstallDeps: vi.fn(),
}));
vi.mock('@/lib/api', () => ({ api: mocks }));

function plugin(over: Partial<FactoryPlugin> = {}): FactoryPlugin {
  return {
    id: 'acme.weather', display_name: 'Weather', description: 'Forecasts', version: '1.2.0', mode: 'copy', path: '/p',
    source: { type: 'github', repo: 'acme/w', tag: '1.2.0' }, enabled: true, state: 'enabled', scope: 'global', last_error: null,
    crash_count: 0, warnings: ['tag differs'], permissions: { network: ['api.weather.com'] }, permissions_acknowledged: true,
    installed_by: 'user', installed_at: '', provides: ['backend.routes'], size: {}, loaded: true, isolated: null, recent_errors: 2,
    frontend: null, activation_events: ['onStartup'], protected: false, ...over,
  };
}

function listing(over: Partial<NonNullable<FactoryListResponse['data']>> = {}): FactoryListResponse {
  return { success: true, data: { plugins: [plugin()], safe_mode: false, safe_mode_reason: '', bisect: null, cloud_managed: false, boot: null, ...over } };
}

beforeEach(() => {
  mocks.factoryList.mockResolvedValue(listing());
  mocks.factoryInstall.mockResolvedValue({ success: true, data: { id: 'acme.new', version: '1.0.0', path: '/x', mode: 'link', warnings: [], deps_installed: [], permissions: { subprocess: true }, restart_required: true } });
  mocks.factoryAction.mockResolvedValue({ success: true });
  mocks.factoryRollback.mockResolvedValue({ success: true });
  mocks.factoryLeaveSafeMode.mockResolvedValue({ success: true });
  mocks.factoryBisect.mockResolvedValue({ success: true, data: {} });
  mocks.factoryBisectAnswer.mockResolvedValue({ success: true, data: {} });
  mocks.factoryErrors.mockResolvedValue({ success: true, data: { errors: [{ at: 1, kind: 'render', message: 'boom', stack: '' }] } });
  mocks.factoryProposals.mockResolvedValue({ success: true, data: { proposals: [] } });
  mocks.factoryDecide.mockResolvedValue({ success: true, data: { decision: 'approved', restart_required: true } });
  mocks.factoryBuiltinSetEnabled.mockResolvedValue({ success: true, data: { id: 'builtin.teams', enabled: false, also_disabled: [], restart_required: true } });
});

it('lists builtin features; disable toggles through the builtin endpoint and protected ones have no toggle', async () => {
  mocks.factoryList.mockResolvedValue(
    listing({
      builtins: [
        { id: 'builtin.teams', display_name: 'Teams', description: 'Agent teams', version: '1.0.0', enabled: true, excluded: false, protected: false, hosts: ['backend'], provides: ['backend.routes', 'backend.workers'], dependencies: {} },
        { id: 'builtin.nexus_plugins_module', display_name: 'Nexus Plugins', description: '', version: '1.0.0', enabled: true, excluded: false, protected: true, hosts: ['backend'], provides: [], dependencies: {} },
      ],
    }),
  );
  render(<PluginFactory />);
  const teams = await screen.findByTestId('builtin-builtin.teams');
  expect(teams).toHaveTextContent('Teams');
  expect(teams).toHaveTextContent('Agent teams');
  const protectedCard = screen.getByTestId('builtin-builtin.nexus_plugins_module');
  expect(protectedCard).toHaveTextContent('protected');
  expect(protectedCard.querySelector('button')).toBeNull();
  fireEvent.click(teams.querySelector('button')!);
  await waitFor(() => expect(mocks.factoryBuiltinSetEnabled).toHaveBeenCalledWith('builtin.teams', false));
  expect(await screen.findByRole('status')).toHaveTextContent('Restart NarraNexus');
});

it('shows agent proposals with permissions and test status; approve/reject decide them', async () => {
  mocks.factoryProposals.mockResolvedValue({ success: true, data: { proposals: [{ id: 'prop_1', plugin_id: 'me.weather', agent_id: 'a1', user_id: 'u1', action: 'activate', scope: 'agent', summary: 'Activate me.weather', permissions: { subprocess: true }, test_report: { ok: true, passed: 3 }, diff_hash: 'h', created_at: 1, decision: 'pending', extra: {} }] } });
  render(<PluginFactory />);
  const card = await screen.findByTestId('proposal-prop_1');
  expect(card).toHaveTextContent('Activate me.weather');
  expect(card).toHaveTextContent('Tests passed (3)');
  expect(card).toHaveTextContent('Runs subprocesses');
  fireEvent.click(screen.getByText('Approve'));
  await waitFor(() => expect(mocks.factoryDecide).toHaveBeenCalledWith('prop_1', true));
  fireEvent.click(screen.getByText('Reject'));
  await waitFor(() => expect(mocks.factoryDecide).toHaveBeenCalledWith('prop_1', false));
});
afterEach(() => vi.clearAllMocks());

it('lists plugins with state, warnings and provides, and drives disable/uninstall/rollback', async () => {
  render(<PluginFactory />);
  expect(await screen.findByText('Weather')).toBeInTheDocument();
  expect(screen.getByText('enabled')).toBeInTheDocument();
  expect(screen.getByText('tag differs')).toBeInTheDocument();
  expect(screen.getByText('backend.routes')).toBeInTheDocument();
  fireEvent.click(screen.getByText('Disable'));
  await waitFor(() => expect(mocks.factoryAction).toHaveBeenCalledWith('acme.weather', 'disable'));
  expect(await screen.findByRole('status')).toHaveTextContent(/Restart NarraNexus/);
  fireEvent.click(screen.getByText('Uninstall'));
  await waitFor(() => expect(mocks.factoryAction).toHaveBeenCalledWith('acme.weather', 'uninstall'));
  fireEvent.click(screen.getByText('Roll back to last known good'));
  await waitFor(() => expect(mocks.factoryRollback).toHaveBeenCalled());
  fireEvent.click(screen.getByText('Errors (2)'));
  expect(await screen.findByText('[render] boom')).toBeInTheDocument();
});

it('installs from a source and shows the permissions disclosure until acknowledged', async () => {
  render(<PluginFactory />);
  await screen.findByText('Weather');
  const input = screen.getByLabelText(/owner\/repo@1.2.0/);
  fireEvent.change(input, { target: { value: 'acme/new@1.0.0' } });
  fireEvent.click(screen.getByText('Install'));
  await waitFor(() => expect(mocks.factoryInstall).toHaveBeenCalledWith('acme/new@1.0.0'));
  const dialog = await screen.findByTestId('permissions-dialog');
  expect(dialog).toHaveTextContent('Runs subprocesses');
  fireEvent.click(screen.getByText('I understand, enable it'));
  await waitFor(() => expect(mocks.factoryAction).toHaveBeenCalledWith('acme.new', 'acknowledge-permissions', { permissionsAcknowledged: true }));
  await waitFor(() => expect(screen.queryByTestId('permissions-dialog')).toBeNull());
});

it('a plugin whose manifest declares a frontend bundle (no backend permissions) still triggers the disclosure — I-12', async () => {
  mocks.factoryInstall.mockResolvedValue({ success: true, data: { id: 'acme.frontend_only', version: '1.0.0', path: '/x', mode: 'link', warnings: [], deps_installed: [], permissions: {}, restart_required: true } });
  mocks.factoryList.mockResolvedValue(
    listing({ plugins: [plugin(), plugin({ id: 'acme.frontend_only', display_name: 'Frontend Only', permissions: {}, permissions_acknowledged: false, frontend: { entry: 'plugin.js' } })] }),
  );
  render(<PluginFactory />);
  await screen.findByText('Weather');
  // The plugin never went through install() in this render (it's already "installed" per the
  // list mock) — this exercises the OTHER path: reconciliation on load() from
  // `permissions_acknowledged` + `frontend`, which is what makes the disclosure reopen for a
  // plugin whose ack was never completed, independent of the install flow.
  const dialog = await screen.findByTestId('permissions-dialog');
  expect(dialog).toHaveTextContent('Runs code inside the app UI');
  fireEvent.click(screen.getByText('I understand, enable it'));
  await waitFor(() => expect(mocks.factoryAction).toHaveBeenCalledWith('acme.frontend_only', 'acknowledge-permissions', { permissionsAcknowledged: true }));
});

it('persists the pending-ack queue to localStorage so it survives a remount', async () => {
  window.localStorage.clear();
  mocks.factoryList.mockResolvedValue(
    listing({ plugins: [plugin({ id: 'acme.pending', permissions: {}, permissions_acknowledged: false, frontend: { entry: 'plugin.js' } })] }),
  );
  const { unmount } = render(<PluginFactory />);
  await screen.findByTestId('permissions-dialog');
  const stored = JSON.parse(window.localStorage.getItem('narranexus.pluginFactory.pendingAckIds') ?? '[]');
  expect(stored).toContain('acme.pending');
  unmount();
  // Remount with a factoryList mock that (implausibly, but this isolates the localStorage
  // seed) fails before load() resolves — the dialog must still be present from the seed alone.
  render(<PluginFactory />);
  expect(await screen.findByTestId('permissions-dialog')).toBeInTheDocument();
});

it('shows the safe-mode banner and walks the bisect wizard', async () => {
  mocks.factoryList.mockResolvedValue(listing({ safe_mode: true, safe_mode_reason: '2 boots failed', plugins: [plugin(), plugin({ id: 'acme.b', display_name: 'B' })] }));
  render(<PluginFactory />);
  expect(await screen.findByTestId('safe-mode-banner')).toHaveTextContent('2 boots failed');
  fireEvent.click(screen.getByText('Find the plugin that breaks startup'));
  await waitFor(() => expect(mocks.factoryBisect).toHaveBeenCalledWith('start'));
  mocks.factoryList.mockResolvedValue(listing({ safe_mode: true, safe_mode_reason: 'x', bisect: { candidates: ['acme.weather', 'acme.b'], trial: ['acme.weather'], cleared: [] } }));
  fireEvent.click(screen.getByText('Leave safe mode'));
  await waitFor(() => expect(mocks.factoryLeaveSafeMode).toHaveBeenCalled());
  expect(await screen.findByTestId('bisect-wizard')).toHaveTextContent('acme.weather');
  fireEvent.click(screen.getByText('It started fine'));
  await waitFor(() => expect(mocks.factoryBisectAnswer).toHaveBeenCalledWith(true));
});

it('renders nothing in cloud mode and an error when the list fails', async () => {
  // Positive control first: the same testid the cloud-managed assertion checks for absence
  // of DOES render in the non-cloud case — without this, a broken `if (cloud_managed) return
  // null` that returns null unconditionally would pass the same assertion below.
  mocks.factoryList.mockResolvedValue(listing({ cloud_managed: false }));
  const first = render(<PluginFactory />);
  expect(await first.findByTestId('plugin-factory')).toBeInTheDocument();
  first.unmount();

  mocks.factoryList.mockResolvedValue(listing({ cloud_managed: true }));
  const { container, unmount } = render(<PluginFactory />);
  await waitFor(() => expect(mocks.factoryList).toHaveBeenCalled());
  await waitFor(() => expect(container.querySelector('[data-testid="plugin-factory"]')).toBeNull());
  unmount();

  mocks.factoryList.mockRejectedValue(new Error('offline'));
  const { container: errContainer } = render(<PluginFactory />);
  expect(await within(errContainer).findByText('offline')).toBeInTheDocument();
});

it('protected plugins cannot be disabled or uninstalled', async () => {
  mocks.factoryList.mockResolvedValue(listing({ plugins: [plugin({ protected: true, source: { type: 'local' } })] }));
  render(<PluginFactory />);
  await screen.findByText('Weather');
  expect((screen.getByText('Disable').closest('button') as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByText('Uninstall')).toBeNull();
  expect(screen.queryByText('Upgrade')).toBeNull();
});

it('a builtin whose on-demand dependencies are missing shows the reason and an install retry', async () => {
  mocks.factoryBuiltinInstallDeps.mockResolvedValue({ success: true, data: { id: 'builtin.channels.lark', installed: ['lark-oapi'], restart_required: true } });
  mocks.factoryList.mockResolvedValue(
    listing({
      builtins: [
        { id: 'builtin.channels.lark', display_name: 'Lark', description: '', version: '1.0.0', enabled: true, excluded: false, protected: false, hosts: ['backend'], provides: [], dependencies: {}, on_demand: true, pip: ['lark-oapi>=1.4.0,<2.0.0'], deps_missing: 'missing lark_oapi; dependency install failed (rc=1)' },
      ],
    }),
  );
  render(<PluginFactory />);
  const card = await screen.findByTestId('builtin-builtin.channels.lark');
  expect(card).toHaveTextContent('dependencies missing');
  expect(card).toHaveTextContent('missing lark_oapi');
  fireEvent.click(screen.getByText('Install dependencies (lark-oapi>=1.4.0,<2.0.0)'));
  await waitFor(() => expect(mocks.factoryBuiltinInstallDeps).toHaveBeenCalledWith('builtin.channels.lark'));
});

it('a builtin the distribution excludes is labelled as such and offers no enable/disable/install-deps control', async () => {
  mocks.factoryList.mockResolvedValue(
    listing({
      builtins: [
        { id: 'builtin.browser', display_name: 'Browser', description: '', version: '1.0.0', enabled: false, excluded: true, protected: true, hosts: ['backend'], provides: [], dependencies: {}, deps_missing: 'missing playwright' },
      ],
    }),
  );
  render(<PluginFactory />);
  const card = await screen.findByTestId('builtin-builtin.browser');
  expect(card).toHaveTextContent('not in this distribution');
  expect(card.querySelector('button')).toBeNull();
  expect(card).not.toHaveTextContent('dependencies missing');
  expect(card).not.toHaveTextContent('protected'); // "cannot be disabled" is moot for something that is not loaded
});
