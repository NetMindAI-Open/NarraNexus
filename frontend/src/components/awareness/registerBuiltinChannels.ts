/**
 * @file_name: registerBuiltinChannels.ts
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: Registers one `ui.channels` row per channel the BACKEND catalog reports, owned by that channel's plugin.
 *
 * Imported for its side effect by `IMChannelsSection`; the registration is a
 * promise (`builtinChannelsReady`) because the rows come from
 * `GET /api/plugins/channels` — the `ingress.channels` registry rendered as
 * data. The section re-renders on registry change, so rows appear when the
 * fetch lands.
 *
 * Why data and not a table here: the six rows used to be written out in this
 * file with a `label`/`icon`/`order` copied verbatim from each plugin's
 * Python `ChannelUi(...)` — two spellings of one fact, kept in sync by hand,
 * with nothing to catch a drift and a fourth edit site for anyone adding a
 * channel. `ChannelUi` is a contract that had no consumer; now it has one.
 *
 * Two things stay code-bound on purpose:
 * - the CONFIG component: a channel's bind form is real UI, not data (a
 *   channel plugin registers its own row with `GenericChannelConfig` if it
 *   has no bespoke form);
 * - the ICON: `ui.icon` is a lucide NAME on the wire, resolved through the
 *   lookup below, because bundling every lucide icon to allow an arbitrary
 *   name would cost far more than the six we ship.
 *
 * Owners are the builtin plugin ids so `disableBuiltinUi('builtin.channels.<x>')`
 * (called at boot, before this lazily-imported chunk ever loads) blacklists the
 * owner: `Registry.register` silently drops that row even though it runs after
 * the disable call, and `IMChannelsSection` never lists it. That is also why
 * the per-row `if (!CHANNELS.has(id))` guards are gone — a blacklisted owner is
 * the registry's job, and re-registering an id it already holds now throws,
 * which is the loud failure a double-registration deserves.
 */
import { Bot, Hash, HelpCircle, MessageCircle, MessageSquare, QrCode, Send, type LucideIcon } from 'lucide-react';
import type { ComponentType } from 'react';

import { api } from '@/lib/api';
import { CHANNELS, type ChannelConfigProps, type ChannelStatus } from '@/platform/registries';

import { DiscordConfig } from './DiscordConfig';
import { LarkConfig } from './LarkConfig';
import { NarramessengerConfig } from './NarramessengerConfig';
import { SlackConfig } from './SlackConfig';
import { TelegramConfig } from './TelegramConfig';
import { WeChatConfig } from './WeChatConfig';

/** lucide names the shipped `ChannelUi.icon` values use. Unknown name → a neutral icon, never a crash. */
const ICONS: Record<string, LucideIcon> = {
  bot: Bot,
  hash: Hash,
  'message-circle': MessageCircle,
  'message-square': MessageSquare,
  'qr-code': QrCode,
  send: Send,
};

/** Bind forms, by channel name. A channel with no entry gets no row (its UI ships with its plugin). */
const CONFIGS: Record<string, ComponentType<ChannelConfigProps>> = {
  discord: DiscordConfig,
  lark: LarkConfig,
  narramessenger: NarramessengerConfig,
  slack: SlackConfig,
  telegram: TelegramConfig,
  wechat: WeChatConfig,
};

/** Bound + enabled → active; bound but disabled → inactive; anything else (unbound, denied, network) → unbound. */
const probe = (channel: string) => async (agentId: string): Promise<ChannelStatus> => {
  try {
    const res = await api.channelCredential(channel, agentId);
    if (!res.success || !res.data) return 'unbound';
    return res.data.enabled ? 'active' : 'inactive';
  } catch {
    return 'unbound';
  }
};

async function registerBuiltinChannels(): Promise<void> {
  let rows;
  try {
    const res = await api.pluginChannels();
    rows = res.success ? (res.data ?? []) : [];
  } catch {
    // The catalog is a plain GET on the same origin that already served the
    // SPA; a failure here means the backend is down, in which case an empty
    // Channels section is the honest rendering and every other panel is
    // failing too. Swallowing it keeps a dead network from breaking module
    // evaluation of the whole Channels chunk.
    return;
  }
  for (const row of rows) {
    const component = CONFIGS[row.name];
    if (!component) continue; // a channel whose config UI is not part of the shell
    CHANNELS.register(
      row.name,
      {
        label: row.ui?.label || row.display_name || row.name,
        icon: ICONS[row.ui?.icon ?? ''] ?? HelpCircle,
        component,
        order: row.ui?.order,
        fetchStatus: probe(row.name),
      },
      { owner: row.owner },
    );
  }
}

/** Resolves once the catalog has been folded into `ui.channels`; tests await it. */
export const builtinChannelsReady: Promise<void> = registerBuiltinChannels();
