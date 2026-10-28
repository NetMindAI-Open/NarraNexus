/**
 * @file_name: BrowserScriptPermissions.test.tsx
 * @description: Script settings remain explicit and never recreate website authorization.
 */
import { afterEach, expect, test, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { BrowserPolicyView } from '@/types/browser';
import BrowserScriptPermissions from '../BrowserScriptPermissions';

afterEach(cleanup);

const site = { origin: 'https://accounts.example.test', full_cdp_access: 'deny' as const };
const policy: BrowserPolicyView = {
  agent_id: 'agent-a', defaults: { full_cdp_access: 'deny' }, origins: [site],
};
const props = () => ({
  agentId: 'agent-a', load: vi.fn().mockResolvedValue(policy),
  saveScripts: vi.fn().mockResolvedValue({ ok: true }),
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => { resolve = res; });
  return { promise, resolve };
}

test('shows only script permissions without access rules or exceptions', async () => {
  render(<BrowserScriptPermissions {...props()} />);
  expect(await screen.findByText(site.origin)).toBeVisible();
  expect(screen.queryByText(/website access|site exceptions|site access|turn approvals|conversation approvals/i)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /allow site|block site/i })).not.toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: /advanced scripts/i })).not.toBeChecked();
});

test('scripts require an explicit save and can be disabled independently', async () => {
  const api = props();
  render(<BrowserScriptPermissions {...api} />);
  fireEvent.click(await screen.findByRole('checkbox', { name: /advanced scripts/i }));
  expect(api.saveScripts).not.toHaveBeenCalled();
  api.load.mockResolvedValue({ ...policy, origins: [{ ...site, full_cdp_access: 'allow' }] });
  fireEvent.click(screen.getByRole('button', { name: /save script/i }));
  await waitFor(() => expect(api.saveScripts).toHaveBeenCalledWith('agent-a', site.origin, true));
  await waitFor(() => expect(screen.getByRole('checkbox')).toBeChecked());
  await waitFor(() => expect(screen.getByRole('checkbox')).toBeEnabled());
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: /save script/i }));
  await waitFor(() => expect(api.saveScripts).toHaveBeenLastCalledWith('agent-a', site.origin, false));
});

test.each(['response', 'exception'])('a %s failure keeps the unsaved choice available to retry', async (kind) => {
  const api = props();
  if (kind === 'response') api.saveScripts.mockResolvedValueOnce({ ok: false });
  else api.saveScripts.mockRejectedValueOnce(new Error('offline'));
  render(<BrowserScriptPermissions {...api} />);
  fireEvent.click(await screen.findByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: /save script/i }));
  await screen.findByRole('alert');
  expect(screen.getByRole('checkbox')).toBeChecked();
  expect(screen.getByRole('button', { name: /save script/i })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: /save script/i }));
  await waitFor(() => expect(api.saveScripts).toHaveBeenCalledTimes(2));
});

test('pending writes disable duplicate actions', async () => {
  const api = props();
  const pending = deferred<{ ok: boolean }>();
  api.saveScripts.mockReturnValue(pending.promise);
  render(<BrowserScriptPermissions {...api} />);
  fireEvent.click(await screen.findByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: /save script/i }));
  expect(screen.getByRole('checkbox')).toBeDisabled();
  expect(screen.getByRole('button', { name: /save script/i })).toBeDisabled();
  await act(async () => { pending.resolve({ ok: false }); });
  expect(screen.getByRole('checkbox')).toBeEnabled();
});

test('switching agents ignores the old in-flight response and clears drafts', async () => {
  const api = props();
  const old = deferred<BrowserPolicyView>();
  api.load.mockImplementation((agentId: string) => agentId === 'agent-a' ? old.promise
    : Promise.resolve({ ...policy, agent_id: agentId, origins: [] }));
  const view = render(<BrowserScriptPermissions {...api} />);
  view.rerender(<BrowserScriptPermissions {...api} agentId="agent-b" />);
  await screen.findByRole('textbox');
  await act(async () => { old.resolve(policy); });
  expect(screen.queryByText(site.origin)).not.toBeInTheDocument();
});

test('load failures provide a retry and do not expose mutation controls', async () => {
  const api = props();
  api.load.mockRejectedValueOnce(new Error('offline'));
  render(<BrowserScriptPermissions {...api} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('offline');
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /refresh/i }));
  expect(await screen.findByText(site.origin)).toBeVisible();
});

test('adding a site creates only a draft until the script permission is saved', async () => {
  const api = props();
  api.load.mockResolvedValue({ ...policy, origins: [] });
  render(<BrowserScriptPermissions {...api} />);
  fireEvent.change(await screen.findByRole('textbox'), { target: { value: 'https://NEW.example:443/' } });
  fireEvent.click(screen.getByRole('button', { name: /add site/i }));
  expect(screen.getByText('https://new.example')).toBeVisible();
  expect(api.saveScripts).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: /save script/i }));
  await waitFor(() => expect(api.saveScripts).toHaveBeenCalledWith('agent-a', 'https://new.example', true));
});

test.each(['file:///private', 'https://user:secret@site.example', 'https://site.example/private'])('rejects an invalid script origin: %s', async (origin) => {
  const api = props();
  render(<BrowserScriptPermissions {...api} />);
  fireEvent.change(await screen.findByRole('textbox'), { target: { value: origin } });
  fireEvent.click(screen.getByRole('button', { name: /add site/i }));
  await screen.findByRole('alert');
  expect(api.saveScripts).not.toHaveBeenCalled();
});
