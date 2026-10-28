/**
 * @file_name: index.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Public entry of the frontend registries (the only import a plugin bundle needs).
 *
 * `REGISTRIES` is the single table of the 18 named registries: `host.ts`
 * (the per-plugin facade) and `loader.ts` (SHELL_REGISTRIES / boot-time
 * lazy gates) both consume it instead of each spelling the same names
 * in their own object/array literal — four copies of that list used to
 * exist (`host.ts` ×3, `loader.ts` ×1); this file is now the one place the
 * next registry gets added (artifact kinds were the 17th, tool renderers
 * the 18th).
 */
import { PAGES } from './pages';
import { SIDEBAR } from './sidebar';
import { PANELS } from './panels';
import { SETTINGS_SECTIONS } from './settingsSections';
import { THEMES } from './themes';
import { COMMANDS } from './commands';
import { CHANNELS } from './channels';
import { ARTIFACT_KINDS } from './artifactKinds';
import {
  AGENT_CARD_BADGES,
  CHAT_HEADER_ACTIONS,
  COMPOSER_EXTENSIONS,
  CONVERSATION_KINDS,
  MESSAGE_ACTIONS,
  MESSAGE_RENDERERS,
  SIDEBAR_SECTIONS,
  TIMELINE_EVENTS,
  TOOL_RENDERERS,
  TOP_BAR_ITEMS,
} from './slotPoints';

export { Registry, RegistryConflictError, disableOwner, enableOwner, isOwnerDisabled } from './registry';
export type { RegistryEntry, RegisterOptions } from './registry';
export { useRegistryEntries } from './hooks';
export { PAGES } from './pages';
export type { PageComponent, PageDef, PageGuard, PageLayout } from './pages';
export { SIDEBAR, sortedSidebarItems } from './sidebar';
export type { SidebarItemDef, SidebarFeatures, SidebarLocation } from './sidebar';
export { PANELS } from './panels';
export type { PanelDef, PanelProps, PanelStripDef } from './panels';
export { SETTINGS_SECTIONS, sortedSettingsSections } from './settingsSections';
export type { SettingsSectionDef, SettingsSectionProps } from './settingsSections';
export { THEMES, applyTheme, clearTheme, validateThemeTokens } from './themes';
export type { ThemeDef } from './themes';
export { COMMANDS } from './commands';
export type { CommandDef } from './commands';
export { CHANNELS, sortedChannels } from './channels';
export type { ChannelConfigProps, ChannelDef, ChannelStatus } from './channels';
export { ARTIFACT_KINDS, validateKindDescriptor } from './artifactKinds';
export type { EditSurface, KindDescriptor, PreviewStrategy, RendererComponent, SaveMode } from './artifactKinds';
export { evaluateWhen, parseWhen } from './when';
export type { WhenClause, WhenContext } from './when';
export {
  AGENT_CARD_BADGES,
  CHAT_HEADER_ACTIONS,
  COMPOSER_EXTENSIONS,
  CONVERSATION_KINDS,
  MESSAGE_ACTIONS,
  MESSAGE_RENDERERS,
  SIDEBAR_SECTIONS,
  TIMELINE_EVENTS,
  TOOL_RENDERERS,
  TOP_BAR_ITEMS,
  bareToolName,
  rendererFor,
  toolRendererFor,
  visibleSlotEntries,
} from './slotPoints';
export type {
  ConversationKindDef,
  MessageRendererDef,
  MessageRendererProps,
  SlotActionContext,
  SlotActionDef,
  SlotComponentDef,
  SlotComponentProps,
  SlotEntryBase,
  TimelineEventDef,
  TimelineEventProps,
  ToolRendererDef,
  ToolRendererProps,
} from './slotPoints';

/** The 18 named registries a plugin (via `HostAPI.registries`) or the loader (via
 *  `SHELL_REGISTRIES`) may touch, keyed by the name `HostAPI.registries` exposes it under. */
export const REGISTRIES = {
  pages: PAGES,
  sidebar: SIDEBAR,
  panels: PANELS,
  settingsSections: SETTINGS_SECTIONS,
  commands: COMMANDS,
  themes: THEMES,
  messageRenderers: MESSAGE_RENDERERS,
  timelineEvents: TIMELINE_EVENTS,
  toolRenderers: TOOL_RENDERERS,
  conversationKinds: CONVERSATION_KINDS,
  channels: CHANNELS,
  artifactKinds: ARTIFACT_KINDS,
  chatHeaderActions: CHAT_HEADER_ACTIONS,
  composerExtensions: COMPOSER_EXTENSIONS,
  messageActions: MESSAGE_ACTIONS,
  sidebarSections: SIDEBAR_SECTIONS,
  agentCardBadges: AGENT_CARD_BADGES,
  topBarItems: TOP_BAR_ITEMS,
} as const;

export type RegistryName = keyof typeof REGISTRIES;
