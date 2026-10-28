/**
 * @file_name: imChannelsRegistry.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-07
 * @description: The Channels section lists rows built from the BACKEND channel catalog: label/icon/order come from the API, owners are the channel plugins, a plugin's own row slots in by order, and a disabled builtin has no row.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MessageSquare } from 'lucide-react';

import { CHANNELS } from '@/platform/registries';
import { IMChannelsSection } from '../IMChannelsSection';
import { builtinChannelsReady } from '../registerBuiltinChannels';

// The catalog the host serves from `ingress.channels`. `vi.hoisted` because the
// `vi.mock` factory below is hoisted above this module's top-level bindings and
// would otherwise read it in its temporal dead zone — the fetch would throw,
// registerBuiltinChannels would swallow it as "backend down", and the suite
// would assert on an empty registry.
//
// Deliberately NOT in the same order as the intended display order, and with `order` values that only
// the API knows: a test that passed with the rows hard-coded in TypeScript
// would have had no way to tell the difference.
const CATALOG = vi.hoisted(() => [
  { name: 'discord', display_name: 'Discord', owner: 'builtin.channels.discord', ui: { label: 'Discord', icon: 'bot', order: 60 } },
  { name: 'lark', display_name: 'Lark', owner: 'builtin.channels.lark', ui: { label: 'Lark / Feishu', icon: 'message-square', order: 10 } },
  { name: 'slack', display_name: 'Slack', owner: 'builtin.channels.slack', ui: { label: 'Slack', icon: 'hash', order: 20 } },
  { name: 'telegram', display_name: 'Telegram', owner: 'builtin.channels.telegram', ui: { label: 'Telegram', icon: 'send', order: 30 } },
  { name: 'wechat', display_name: 'WeChat', owner: 'builtin.channels.wechat', ui: { label: 'WeChat', icon: 'qr-code', order: 40 } },
  { name: 'narramessenger', display_name: 'NarraMessenger', owner: 'builtin.channels.narramessenger', ui: { label: 'NarraMessenger', icon: 'message-circle', order: 50 } },
  // A channel the host loaded whose bind form is not part of the shell: no row.
  { name: 'acme_remote', display_name: 'Acme Remote', owner: 'acme.remote', ui: { label: 'Acme Remote', icon: 'bot', order: 15 } },
]);

const unbound = vi.fn(async () => ({ success: false }));
vi.mock('@/lib/api', () => ({
  api: {
    channelCredential: (...a: unknown[]) => unbound(...a),
    pluginChannels: async () => ({ success: true, data: CATALOG }),
  },
}));
vi.mock('@/stores', () => ({ useConfigStore: () => ({ agentId: 'a1' }) }));

const disposers: (() => void)[] = [];
afterEach(() => disposers.splice(0).forEach((d) => d()));

describe('channels registry', () => {
  it('rows come from the backend catalog, owned by their channel plugins', async () => {
    await builtinChannelsReady;
    render(<IMChannelsSection />);
    const owners = Object.fromEntries(CHANNELS.list().map((e) => [e.id, e.owner]));
    expect(owners).toMatchObject({ lark: 'builtin.channels.lark', discord: 'builtin.channels.discord', wechat: 'builtin.channels.wechat' });
    // Labels are the API's, not a copy in this repo's TypeScript.
    expect(screen.getByText('Lark / Feishu')).toBeInTheDocument();
    expect(screen.getByText('Discord')).toBeInTheDocument();
    // order came from the catalog, so the six render in the host's order.
    const labels = ['Lark / Feishu', 'Slack', 'Telegram'].map((l) => screen.getByText(l));
    expect(labels[0].compareDocumentPosition(labels[1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(labels[1].compareDocumentPosition(labels[2]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('a catalog channel with no shell config component gets no row', async () => {
    await builtinChannelsReady;
    expect(CHANNELS.has('acme_remote')).toBe(false);
    expect(CATALOG.some((c) => c.name === 'acme_remote')).toBe(true);
  });

  it('a plugin channel appears in order and a disabled builtin disappears', async () => {
    await builtinChannelsReady;
    disposers.push(CHANNELS.register('acme_chat', { label: 'Acme Chat', icon: MessageSquare, order: 25, component: () => <p>acme config</p>, fetchStatus: async () => 'active' }, { owner: 'acme.chat' }));
    const larkDef = CHANNELS.get('lark')!;
    CHANNELS.removeOwner('builtin.channels.lark');
    disposers.push(() => CHANNELS.register('lark', larkDef, { owner: 'builtin.channels.lark' }));
    render(<IMChannelsSection />);
    expect(screen.queryByText('Lark / Feishu')).toBeNull();
    const labels = ['Slack', 'Acme Chat', 'Telegram'].map((l) => screen.getByText(l));
    expect(labels[0].compareDocumentPosition(labels[1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(labels[1].compareDocumentPosition(labels[2]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await waitFor(() => expect(unbound).toHaveBeenCalled());
  });
});
