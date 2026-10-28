/**
 * @file PluginsSettings.test.tsx
 * @description State-machine coverage for the Plugins panel: not-installed
 * shows an Install button, installed shows the version + Uninstall, and a
 * cloud-managed deployment hides the panel entirely (installing centrally
 * managed plugins locally would just 403). `api` is mocked — no network.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { PluginsSettings } from '../PluginsSettings';
import type { PluginInstallEvent, PluginStatus } from '@/types';

const mockGetPlugins = vi.fn();
const mockInstallPlugin = vi.fn();
const mockUninstallPlugin = vi.fn();
vi.mock('@/lib/api', () => ({
  api: {
    getPlugins: (...a: unknown[]) => mockGetPlugins(...a),
    installPlugin: (...a: unknown[]) => mockInstallPlugin(...a),
    uninstallPlugin: (...a: unknown[]) => mockUninstallPlugin(...a),
  },
}));

const NOT_INSTALLED: PluginStatus = {
  id: 'codex_cli',
  display_name: 'Codex CLI',
  installed: false,
  version: null,
  target_version: '1.2.0',
  update_available: false,
  logged_in: false,
  size_hint: '~120MB',
  busy: false,
};

const INSTALLED: PluginStatus = {
  id: 'claude_code',
  display_name: 'Claude Code',
  installed: true,
  version: '2.0.0',
  target_version: '2.0.0',
  update_available: false,
  logged_in: true,
  size_hint: '~80MB',
  busy: false,
};

beforeEach(() => {
  mockGetPlugins.mockReset();
  mockInstallPlugin.mockReset();
  mockUninstallPlugin.mockReset();
});

afterEach(() => vi.restoreAllMocks());

test('a not-installed plugin shows an Install button, not Uninstall', async () => {
  mockGetPlugins.mockResolvedValue({
    success: true,
    data: { plugins: [NOT_INSTALLED], cloud_managed: false },
  });
  render(<PluginsSettings />);
  expect(await screen.findByText('Codex CLI')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Install/ })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Uninstall/ })).toBeNull();
});

test('an installed plugin shows its version and an Uninstall button', async () => {
  mockGetPlugins.mockResolvedValue({
    success: true,
    data: { plugins: [INSTALLED], cloud_managed: false },
  });
  render(<PluginsSettings />);
  expect(await screen.findByText('Claude Code')).toBeInTheDocument();
  expect(screen.getByText('v2.0.0')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Uninstall/ })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /^Install/ })).toBeNull();
});

test('cloud_managed hides the panel entirely', async () => {
  mockGetPlugins.mockResolvedValue({
    success: true,
    data: { plugins: [NOT_INSTALLED, INSTALLED], cloud_managed: true },
  });
  const { container } = render(<PluginsSettings />);
  await waitFor(() => expect(mockGetPlugins).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
  expect(screen.queryByText('Codex CLI')).toBeNull();
});

test('clicking Install streams progress lines and lands on Uninstall once done', async () => {
  mockGetPlugins
    .mockResolvedValueOnce({ success: true, data: { plugins: [NOT_INSTALLED], cloud_managed: false } })
    .mockResolvedValueOnce({
      success: true,
      data: { plugins: [{ ...NOT_INSTALLED, installed: true, version: '1.2.0' }], cloud_managed: false },
    });
  let resolveInstall: (v: PluginInstallEvent) => void = () => {};
  const installDone = new Promise<PluginInstallEvent>((resolve) => { resolveInstall = resolve; });
  mockInstallPlugin.mockImplementation(
    async (_id: string, onEvent: (e: PluginInstallEvent) => void) => {
      onEvent({ done: false, phase: 'pip', line: 'Collecting openai-codex-cli-bin' });
      const final = await installDone;
      onEvent(final);
      return final;
    },
  );

  render(<PluginsSettings />);
  await screen.findByText('Codex CLI');
  fireEvent.click(screen.getByRole('button', { name: /Install/ }));

  // Mid-flight: the progress line is visible before the install resolves.
  expect(await screen.findByText('Collecting openai-codex-cli-bin')).toBeInTheDocument();

  resolveInstall({
    done: true,
    ok: true,
    error: null,
    status: { ...NOT_INSTALLED, installed: true, version: '1.2.0' },
  });

  await waitFor(() => expect(mockGetPlugins).toHaveBeenCalledTimes(2));
  expect(await screen.findByRole('button', { name: /Uninstall/ })).toBeInTheDocument();
});

test('a failed install surfaces the error and keeps the Install button', async () => {
  mockGetPlugins.mockResolvedValue({
    success: true,
    data: { plugins: [NOT_INSTALLED], cloud_managed: false },
  });
  mockInstallPlugin.mockImplementation(
    async (_id: string, onEvent: (e: PluginInstallEvent) => void) => {
      const final: PluginInstallEvent = { done: true, ok: false, error: 'disk full', status: null };
      onEvent(final);
      return final;
    },
  );

  render(<PluginsSettings />);
  await screen.findByText('Codex CLI');
  fireEvent.click(screen.getByRole('button', { name: /Install/ }));

  expect(await screen.findByText('disk full')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Install/ })).toBeInTheDocument();
});

test('installed but version unknown shows "version unknown" and an Update button', async () => {
  // N8 backend semantics: binary present but `--version` unreadable →
  // installed:true, version:null. UI must not render `vnull`, and must still
  // offer a fix action (Update/reinstall), not leave only Uninstall.
  mockGetPlugins.mockResolvedValue({
    success: true,
    data: { plugins: [{ ...INSTALLED, version: null }], cloud_managed: false },
  });
  render(<PluginsSettings />);
  expect(await screen.findByText('Claude Code')).toBeInTheDocument();
  expect(screen.getByText(/version unknown/i)).toBeInTheDocument();
  expect(screen.queryByText('vnull')).toBeNull();
  expect(screen.getByRole('button', { name: /Update/i })).toBeInTheDocument();
});
