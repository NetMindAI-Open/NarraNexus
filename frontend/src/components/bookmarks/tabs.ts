/**
 * @file_name: tabs.ts
 * @author:
 * @date: 2026-06-11
 * @description: The atomic-tab registry for the bookmark strip.
 *
 * Owner-decided IA (2026-06-11): the smallest unit is an atomic tab —
 * ONE tab opens exactly ONE panel, never a stack of sections to scroll
 * through. Categories group the atomic tabs visually on the strip.
 *
 * This file is the single source of truth for: tab ids, labels, icons,
 * category grouping, and how bookmarkStore signals map onto each tab's
 * status (spinner / attention pulse / info dot / badge count).
 */

import {
  Sparkles,
  FolderOpen,
  Radio,
  Network,
  ListTodo,
  Inbox,
  Puzzle,
  Server,
  BookOpen,
  type LucideIcon,
} from 'lucide-react';
import type { AgentBookmarkState } from '@/stores/bookmarkStore';
import { useBookmarkStore } from '@/stores/bookmarkStore';

export type AtomicTabId =
  | 'awareness'
  | 'workspace'
  | 'channels'
  | 'social'
  | 'jobs'
  | 'inbox'
  | 'skills'
  | 'mcp'
  | 'memory';

export interface AtomicTabDef {
  id: AtomicTabId;
  label: string;
  icon: LucideIcon;
  /** Short caption for the 64px strip when label is too long. */
  stripLabel?: string;
}

export interface StripCategory {
  label: string;
  tabs: AtomicTabDef[];
}

/** Strip layout, top to bottom. ONE content per tab — no nested stacks. */
export const STRIP_CATEGORIES: StripCategory[] = [
  {
    label: 'Config',
    tabs: [
      { id: 'awareness', label: 'Awareness', icon: Sparkles },
      { id: 'workspace', label: 'Workspace', icon: FolderOpen },
      { id: 'channels', label: 'Channels', icon: Radio },
    ],
  },
  {
    label: 'Activity',
    tabs: [
      { id: 'jobs', label: 'Jobs', icon: ListTodo },
      { id: 'inbox', label: 'Inbox', icon: Inbox },
    ],
  },
  {
    label: 'Network',
    tabs: [{ id: 'social', label: 'Social Network', icon: Network, stripLabel: 'Social' }],
  },
  {
    label: 'Skills',
    tabs: [
      { id: 'skills', label: 'Skills', icon: Puzzle },
      { id: 'mcp', label: 'MCP Servers', icon: Server },
    ],
  },
  {
    label: 'Memory',
    tabs: [{ id: 'memory', label: 'Memory', icon: BookOpen }],
  },
];

export const ALL_TABS: AtomicTabDef[] = STRIP_CATEGORIES.flatMap((c) => c.tabs);

export function tabLabel(id: AtomicTabId): string {
  return ALL_TABS.find((t) => t.id === id)?.label ?? id;
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
