/**
 * @file_name: slotPoints.ts
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: The shell's slot points and the two content registries (spec §658): where a plugin adds UI *inside* existing surfaces.
 *
 * Structural registries (pages, panels, commands, …) add whole surfaces.
 * These add to surfaces the shell already draws: an action in the chat
 * header's ⋯ menu, a control strip above the composer, a hover action on
 * a message, a section under the sidebar nav, a badge on an agent row, an
 * item in the top bar — each gated by a `when` predicate the host
 * evaluates (`when.ts`) and sorted by `order` (builtins use 10, 20, …).
 * `messageRenderers` / `timelineEvents` let a plugin own the rendering of
 * a message it recognises or of a timeline event type the shell does not
 * know; `toolRenderers` own the output row of one named tool;
 * `conversationKinds` names the kinds `when: conversationKind:<k>`
 * may refer to (the shell registers `chat`, builtin.teams `team`).
 */
import type { ComponentType } from 'react';
import type { LucideIcon } from 'lucide-react';

import { Registry, type RegistryEntry } from './registry';
import { evaluateWhen, parseWhen, type WhenClause, type WhenContext } from './when';

export interface SlotEntryBase {
  /** Visibility predicate(s); see when.ts. Validated at registration. */
  when?: WhenClause | WhenClause[];
  /** Sort key within the slot; builtins use 10, 20, … so plugins can slot between. Default 100. */
  order?: number;
}

export interface SlotComponentProps {
  agentId: string | null;
}

/** A component the slot mounts (composer extension, sidebar section, agent-card badge, top-bar item). */
export interface SlotComponentDef extends SlotEntryBase {
  component: ComponentType<SlotComponentProps>;
}

export interface SlotActionContext {
  agentId: string | null;
  /** Present for message actions. */
  message?: unknown;
}

/** An action the slot lists (chat header ⋯ menu, message hover actions). */
export interface SlotActionDef extends SlotEntryBase {
  /** Literal label, or an i18n key when `labelIsKey` is set (plugins use `plugin:<id>` keys). */
  label: string;
  labelIsKey?: boolean;
  icon?: LucideIcon;
  run: (ctx: SlotActionContext) => void | Promise<void>;
}

export interface ConversationKindDef {
  labelKey: string;
  icon?: LucideIcon;
}

export interface MessageRendererProps {
  message: unknown;
  agentId?: string;
  isStreaming?: boolean;
}

/** Owns the rendering of a message it recognises; the first matching renderer (by `order`) wins, else the shell renders. */
export interface MessageRendererDef {
  match: (message: unknown) => boolean;
  component: ComponentType<MessageRendererProps>;
  order?: number;
}

export interface TimelineEventProps {
  event: { id: string; type: string; [key: string]: unknown };
  isStreaming?: boolean;
}

/** Renders a timeline event `type` the shell's TurnTimeline does not know; the entry id IS the event type. */
export interface TimelineEventDef {
  component: ComponentType<TimelineEventProps>;
}

export interface ToolRendererProps {
  /** The tool name as it arrived (possibly `mcp__<server>__<tool>`). */
  toolName: string;
  /** The persisted output string, exactly as the generic row would show it. */
  output: string;
  isStreaming?: boolean;
}

/**
 * Owns the OUTPUT row of one tool; the entry id is the bare tool name (the
 * `mcp__<server>__` namespace stripped), so a renderer never depends on which
 * server id a deployment mounted the tool under. The bare name is therefore a
 * GLOBAL key: `mcp__chat__read` and `mcp__browser__read` share the id `read`,
 * so register only names unique across modules (`browser_look`, not `read`);
 * the registry refuses a second claimant. `accepts` lets it decline an
 * output shape it cannot read; a declined, unregistered or crashing renderer
 * leaves the shell's generic row, so the output is never lost.
 */
export interface ToolRendererDef {
  component: ComponentType<ToolRendererProps>;
  accepts?: (output: string) => boolean;
}

function validated<T extends SlotEntryBase>(kind: string): Registry<T> {
  // M-11: `Registry`'s `validate` constructor option is the one way to build a validating
  // registry — this used to overwrite the instance's own `register` property, one of two
  // functionally-identical patterns coexisting in this codebase (see `themes.ts`'s mirror doc
  // for the other, a subclass override, now unified on this same mechanism).
  return new Registry<T>(kind, {
    validate: (value) => {
      parseWhen(value.when); // throws on an unknown clause — never "always visible" by accident
    },
  });
}

export const CONVERSATION_KINDS = new Registry<ConversationKindDef>('ui.conversationKinds');
export const CHAT_HEADER_ACTIONS = validated<SlotActionDef>('ui.chatHeaderActions');
export const COMPOSER_EXTENSIONS = validated<SlotComponentDef>('ui.composerExtensions');
export const MESSAGE_ACTIONS = validated<SlotActionDef>('ui.messageActions');
export const SIDEBAR_SECTIONS = validated<SlotComponentDef>('ui.sidebarSections');
export const AGENT_CARD_BADGES = validated<SlotComponentDef>('ui.agentCardBadges');
export const TOP_BAR_ITEMS = validated<SlotComponentDef>('ui.topBarItems');
export const MESSAGE_RENDERERS = new Registry<MessageRendererDef>('ui.messageRenderers');
export const TIMELINE_EVENTS = new Registry<TimelineEventDef>('ui.timelineEvents');
export const TOOL_RENDERERS = new Registry<ToolRendererDef>('ui.toolRenderers');

/** The bare tool name a `TOOL_RENDERERS` entry is keyed by: `mcp__chat__read` → `read`. */
export function bareToolName(toolName: string): string {
  const parts = toolName.split('__');
  return parts[parts.length - 1] || toolName;
}

/** The registered renderer entry that will draw this output, if any. */
export function toolRendererFor(
  entries: RegistryEntry<ToolRendererDef>[],
  toolName: string,
  output: string,
): RegistryEntry<ToolRendererDef> | undefined {
  if (!toolName) return undefined;
  const entry = entries.find((e) => e.id === bareToolName(toolName));
  if (!entry) return undefined;
  try {
    return !entry.value.accepts || entry.value.accepts(output) ? entry : undefined;
  } catch {
    return undefined;
  }
}

/** The entries to draw for a slot: `when` filtered against `ctx`, sorted by `order`. */
export function visibleSlotEntries<T extends SlotEntryBase>(entries: RegistryEntry<T>[], ctx: WhenContext): RegistryEntry<T>[] {
  return entries
    .filter((e) => evaluateWhen(e.value.when, ctx))
    .sort((a, b) => (a.value.order ?? 100) - (b.value.order ?? 100));
}

/** The renderer that owns `message`, if any (lowest `order` among matches). */
export function rendererFor(entries: RegistryEntry<MessageRendererDef>[], message: unknown): MessageRendererDef | undefined {
  return entries
    .filter((e) => {
      try {
        return e.value.match(message);
      } catch {
        return false;
      }
    })
    .sort((a, b) => (a.value.order ?? 100) - (b.value.order ?? 100))[0]?.value;
}
