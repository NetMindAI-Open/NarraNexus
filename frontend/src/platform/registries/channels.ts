/**
 * @file_name: channels.ts
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: IM channel registry — the rows of the Channels section (label, icon, config component, status probe).
 *
 * Replaces the hard-coded `IM_CHANNELS` list in `IMChannelsSection`. Each
 * builtin channel registers its row with its own plugin id as owner
 * (`components/awareness/registerBuiltinChannels.ts`), so disabling
 * `builtin.channels.<x>` removes the row; a channel plugin registers its
 * own (usually with the schema-driven `GenericChannelConfig`).
 */
import type { ComponentType } from 'react';
import type { LucideIcon } from 'lucide-react';

import { Registry, type RegistryEntry } from './registry';

export interface ChannelConfigProps {
  onBindStateChange?: () => void;
}

/** Tri-state: a credential can exist but be inactive (imported from a bundle, awaiting activation). */
export type ChannelStatus = 'active' | 'inactive' | 'unbound';

export interface ChannelDef {
  /** Literal label, or an i18n key when `labelIsKey` is set. */
  label: string;
  labelIsKey?: boolean;
  icon: LucideIcon;
  component: ComponentType<ChannelConfigProps>;
  /** Bound + active / bound-but-inactive / no credential, for one agent. */
  fetchStatus: (agentId: string) => Promise<ChannelStatus>;
  /** Sort key; builtins use 10, 20, … so plugins can slot between. */
  order?: number;
}

export const CHANNELS = new Registry<ChannelDef>('ui.channels');

export function sortedChannels(entries: RegistryEntry<ChannelDef>[] = CHANNELS.list()): RegistryEntry<ChannelDef>[] {
  return [...entries].sort((a, b) => (a.value.order ?? 100) - (b.value.order ?? 100));
}
