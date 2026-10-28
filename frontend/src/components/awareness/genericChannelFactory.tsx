/**
 * @file_name: genericChannelFactory.tsx
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: `makeGenericChannelConfig(channel)` — the Channels-row component a channel plugin registers into `ui.channels`, bound to one channel.
 *
 * Separate from GenericChannelConfig.tsx so that file exports only the
 * component (react-refresh only-export-components).
 */
import type { ComponentType } from 'react';

import type { ChannelConfigProps } from '@/platform/registries';

import { GenericChannelConfig } from './GenericChannelConfig';

export function makeGenericChannelConfig(channel: string): ComponentType<ChannelConfigProps> {
  const Bound = (props: ChannelConfigProps) => <GenericChannelConfig channel={channel} {...props} />;
  Bound.displayName = `GenericChannelConfig(${channel})`;
  return Bound;
}
