/**
 * @file_name: types.ts
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: The host-facing types a frontend plugin programs against. They are the app's own definitions
 * (single source of truth, under frontend/src/platform) re-exported here so the published package and the
 * running host can never disagree; the package build emits them as .d.ts.
 */
export type { Disposable, HostAPI } from '../../../src/platform/host';
export type {
  ChannelConfigProps,
  ChannelDef,
  ChannelStatus,
  CommandDef,
  ConversationKindDef,
  MessageRendererDef,
  MessageRendererProps,
  PageDef,
  PanelDef,
  SettingsSectionDef,
  SidebarItemDef,
  SlotActionContext,
  SlotActionDef,
  SlotComponentDef,
  SlotComponentProps,
  ThemeDef,
  TimelineEventDef,
  TimelineEventProps,
  WhenClause,
  WhenContext,
} from '../../../src/platform/registries';
