/**
 * @file_name: tabs.ts
 * @author:
 * @date: 2026-06-11
 * @description: The atomic-tab strip, DERIVED from the `PANELS` registry's `strip` metadata (was a hardcoded literal table until 2026-09-07 — see the changelog entry below).
 *
 * Owner-decided IA (2026-06-11): the smallest unit is an atomic tab —
 * ONE tab opens exactly ONE panel, never a stack of sections to scroll
 * through. Categories group the atomic tabs visually on the strip.
 *
 * `stripCategories()` / `allTabs()` / `builtinTabIds()` are functions, not
 * precomputed constants: `platform/builtin.ts` (which populates `PANELS`)
 * itself imports `ArtifactsGlyph` from this file for one panel's icon, so a
 * module-level `const STRIP_CATEGORIES = ...` evaluated at THIS file's
 * first import would run partway through `builtin.ts`'s own registration
 * pass and freeze on an incomplete (or empty) `PANELS` forever. Computing
 * on every call costs a sort over ~a dozen entries — cheap, and immune to
 * import-order accidents by construction.
 */

import { createElement, forwardRef } from 'react';
import type { LucideIcon, LucideProps } from 'lucide-react';
import type { AgentBookmarkState } from '@/stores/bookmarkStore';
import { useBookmarkStore } from '@/stores/bookmarkStore';
import { PANELS, type PanelDef, type RegistryEntry } from '@/platform/registries';

/**
 * Artifacts glyph — node-circle joined to a frame (Owner-provided reference,
 * 2026-08-06 screenshot). Not in lucide, so drawn locally with the same
 * stroke conventions; typed as LucideIcon so the registry / palette / header
 * can use it interchangeably.
 */
export const ArtifactsGlyph = forwardRef<SVGSVGElement, LucideProps>(
  function ArtifactsGlyph({ size = 24, strokeWidth = 2, ...props }, ref) {
    return createElement(
      'svg',
      {
        ref,
        xmlns: 'http://www.w3.org/2000/svg',
        width: size,
        height: size,
        viewBox: '0 0 24 24',
        fill: 'none',
        stroke: 'currentColor',
        strokeWidth,
        strokeLinecap: 'round',
        strokeLinejoin: 'round',
        ...props,
      },
      createElement('circle', { cx: 6, cy: 17, r: 3, key: 'c' }),
      createElement('path', { d: 'M8.2 14.8 12 11', key: 'l' }),
      createElement('rect', { x: 12, y: 3.5, width: 8.5, height: 8.5, rx: 1.5, key: 'r' }),
    );
  },
) as unknown as LucideIcon;

/** A tab id is any registered `PANELS` id with `strip` metadata; plugins may register
 *  further ids (any string), so consumers must not exhaustively switch on a builtin list. */
export type AtomicTabId = string;

export interface AtomicTabDef {
  id: AtomicTabId;
  label: string;
  /** i18n key (namespace `rail`) for the label; `label` is the fallback. */
  labelKey: string;
  icon: LucideIcon;
  /** Short caption for the 64px strip when label is too long. */
  stripLabel?: string;
  /** i18n key for `stripLabel` when present. */
  stripLabelKey?: string;
  /**
   * Offered only in a specific context. `'studio'`: the creation studio is
   * open — or can be resumed — on the current agent, i.e. this agent went
   * through "Create with AI" and has not pressed Done. Never for an agent that
   * did not: a studio panel with no conversation driving it reads as broken.
   * The tab stays REGISTERED regardless (so
   * `tabLabelKey` / `tabDescKey` resolve for a drawer that is already on it);
   * only the pickable lists — the chat header's ⋯ menu, the ⌘K palette, the drawer's title switcher — filter on it.
   * One field here rather than a filter in each consumer — but the rule is
   * applied ONLY by `visibleTabs(ctx)` / `visibleCategories(ctx)` (both via
   * `tabOffered`). `stripCategories()` / `allTabs()` are
   * the UNFILTERED registry, for looking a def up by id (the chat header's
   * `ALL_TAB_DEFS`) and resolving the drawer title; a new panel entry must go
   * through `visibleTabs`, or it will offer this tab to every agent.
   */
  conditional?: 'studio';
}

/** What the pickable lists need to know to decide what to offer. */
export interface TabVisibilityContext {
  studioOpen: boolean;
  /** The studio was collapsed on this agent and can be picked up again. */
  studioResumable: boolean;
}

export interface StripCategory {
  label: string;
  /** i18n key (namespace `rail`) for the category label; `label` is the fallback. */
  labelKey: string;
  tabs: AtomicTabDef[];
  /** When set, the group shows a colored brand header (instead of just the
   *  divider hairline). Used for the Narra/Nexus spine. */
  title?: string;
  /** i18n key for the brand `title` when present. */
  titleKey?: string;
  /** Brand accent for the title: carbon (Narra) or silicon (Nexus). */
  accent?: 'carbon' | 'silicon';
}

/**
 * Category branding (label, optional brand header/accent) stays a small
 * fixed table — unlike per-tab metadata, this is shell IA, not something a
 * panel registration should restate. A plugin panel names a category by
 * key (`PanelStripDef.category`); an unknown key falls back to `config`.
 * Order here is strip top-to-bottom order.
 */
const CATEGORY_ORDER = ['config', 'activity', 'narra', 'nexus', 'skills'] as const;
type CategoryKey = (typeof CATEGORY_ORDER)[number];
const CATEGORY_META: Record<CategoryKey, Omit<StripCategory, 'tabs'>> = {
  config: { label: 'Config', labelKey: 'rail.category.config' },
  activity: { label: 'Activity', labelKey: 'rail.category.activity' },
  narra: { label: 'Narra', labelKey: 'rail.category.narra', title: 'Narra', titleKey: 'rail.brand.narra', accent: 'carbon' },
  nexus: { label: 'Nexus', labelKey: 'rail.category.nexus', title: 'Nexus', titleKey: 'rail.brand.nexus', accent: 'silicon' },
  skills: { label: 'Skills', labelKey: 'rail.category.skills' },
};

function isCategoryKey(key: string | undefined): key is CategoryKey {
  return !!key && (CATEGORY_ORDER as readonly string[]).includes(key);
}

function tabDefFor(entry: RegistryEntry<PanelDef>): AtomicTabDef | undefined {
  const strip = entry.value.strip;
  if (!strip) return undefined;
  return {
    id: entry.id,
    label: strip.label,
    labelKey: strip.labelKey,
    icon: strip.icon,
    stripLabel: strip.stripLabel,
    stripLabelKey: strip.stripLabelKey,
    conditional: strip.conditional,
  };
}

/** Strip layout, top to bottom, derived from `PANELS` entries that declared `strip`
 *  metadata — see the file header for why this is a function, not a cached constant. */
export function stripCategories(entries: RegistryEntry<PanelDef>[] = PANELS.list()): StripCategory[] {
  const byCategory = new Map<CategoryKey, AtomicTabDef[]>();
  const sorted = [...entries].sort((a, b) => (a.value.strip?.order ?? 100) - (b.value.strip?.order ?? 100));
  for (const entry of sorted) {
    const def = tabDefFor(entry);
    if (!def) continue;
    const key = isCategoryKey(entry.value.strip?.category) ? entry.value.strip!.category! : 'config';
    const list = byCategory.get(key as CategoryKey) ?? [];
    list.push(def);
    byCategory.set(key as CategoryKey, list);
  }
  return CATEGORY_ORDER.filter((key) => byCategory.has(key)).map((key) => ({ ...CATEGORY_META[key], tabs: byCategory.get(key)! }));
}

/** Every strip-visible tab, unfiltered (see `AtomicTabDef.conditional` doc for why
 *  "unfiltered" is the correct default here). */
export function allTabs(entries: RegistryEntry<PanelDef>[] = PANELS.list()): AtomicTabDef[] {
  return stripCategories(entries).flatMap((c) => c.tabs);
}

/** The ids of every builtin (owner `builtin.ui`) strip tab — used by `builtin.test.ts`
 *  to assert every one of them resolves to a real panel component. */
export function builtinTabIds(entries: RegistryEntry<PanelDef>[] = PANELS.list()): AtomicTabId[] {
  return entries.filter((e) => e.owner === 'builtin.ui' && e.value.strip).map((e) => e.id);
}

function tabOffered(tab: AtomicTabDef, ctx: TabVisibilityContext): boolean {
  return tab.conditional !== 'studio' || ctx.studioOpen || ctx.studioResumable;
}

/**
 * The panels a user may PICK from right now, grouped by strip category —
 * `stripCategories()` minus the conditional tabs whose context does not
 * hold; categories left empty by the filter are dropped. This is the SINGLE
 * place visibility is decided: the drawer's title switcher consumes it
 * directly and `visibleTabs` is its flattening, so every entry point that
 * offers panels sees the same set.
 */
export function visibleCategories(ctx: TabVisibilityContext, entries: RegistryEntry<PanelDef>[] = PANELS.list()): StripCategory[] {
  return stripCategories(entries)
    .map((c) => ({ ...c, tabs: c.tabs.filter((t) => tabOffered(t, ctx)) }))
    .filter((c) => c.tabs.length > 0);
}

/**
 * `visibleCategories` flattened — for the flat entry points (the chat
 * header's ⋯ menu, the ⌘K palette). Derived from, never parallel to, the
 * grouped view, so a conditional tab can never leak out of one entry while
 * being hidden in another.
 */
export function visibleTabs(ctx: TabVisibilityContext, entries: RegistryEntry<PanelDef>[] = PANELS.list()): AtomicTabDef[] {
  return visibleCategories(ctx, entries).flatMap((c) => c.tabs);
}

export function tabLabel(id: AtomicTabId): string {
  return allTabs().find((t) => t.id === id)?.label ?? id;
}

/** i18n key (namespace `rail`) for a tab's label, for consumers with a `t`. */
export function tabLabelKey(id: AtomicTabId): string {
  return allTabs().find((t) => t.id === id)?.labelKey ?? `rail.${id}`;
}

/**
 * i18n key for a tab's one-sentence explainer — what the panel is for and
 * how a user works with it. Shown behind the ? icon in the drawer header
 * and as hover text on the detail-menu items, so users who never read the
 * docs still get oriented (Owner 2026-08-06).
 */
export function tabDescKey(id: AtomicTabId): string {
  return `rail.desc.${id}`;
}

// ---------------------------------------------------------------------------
// bookmarkStore signal → tab status mapping
// ---------------------------------------------------------------------------

export interface TabStatus {
  status: 'running' | 'attention' | 'info' | 'none';
  badge?: number;
}

/** Which bookmarkStore highlight keys belong to a tab. */
function keysForTab(state: AgentBookmarkState, id: AtomicTabId): string[] {
  switch (id) {
    case 'jobs':
      return Object.keys(state.highlights).filter((k) => k.startsWith('job:'));
    case 'inbox':
      return state.highlights['inbox'] ? ['inbox'] : [];
    case 'awareness':
      return state.highlights['profile:awareness'] ? ['profile:awareness'] : [];
    default:
      return [];
  }
}

/** Derive the visual status of one atomic tab from the agent's state. */
export function deriveTabStatus(
  state: AgentBookmarkState | undefined,
  id: AtomicTabId,
): TabStatus {
  if (!state) return { status: 'none' };

  if (id === 'jobs') {
    if (state.badges.failedJobs > 0) {
      return { status: 'attention', badge: state.badges.failedJobs };
    }
    if (state.subBookmarks.some((s) => s.key.startsWith('job:') && s.status === 'running')) {
      return { status: 'running' };
    }
    if (keysForTab(state, id).some((k) => state.highlights[k] === 'info')) {
      return { status: 'info' };
    }
    return { status: 'none' };
  }

  if (id === 'inbox') {
    if (state.badges.inboxUnread > 0) {
      return { status: 'attention', badge: state.badges.inboxUnread };
    }
    return { status: 'none' };
  }

  if (id === 'awareness') {
    if (state.highlights['profile:awareness']) return { status: 'info' };
    return { status: 'none' };
  }

  return { status: 'none' };
}

/**
 * User opened a tab — clear its 'info' highlights (attention/badges only
 * clear when the underlying condition is resolved; see bookmarkStore).
 */
export function markTabOpened(agentId: string, id: AtomicTabId): void {
  const store = useBookmarkStore.getState();
  const state = store.agents[agentId];
  if (!state) return;
  for (const key of keysForTab(state, id)) {
    store.markOpened(agentId, key);
  }
}
