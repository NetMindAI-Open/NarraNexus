/**
 * @file_name: uiStore.ts
 * @date: 2026-06-22
 * @description: Small UI-only store for layout chrome state that several
 * components share but no backend cares about.
 *
 * Holds: the mobile navigation drawer, the desktop sidebar collapse, the
 * command-palette open flag, and cross-component panel requests. On < lg the
 * agent-list sidebar is an off-canvas overlay; the mobile top strip's
 * hamburger toggles it, MainLayout renders its backdrop, and Sidebar closes
 * it on navigation. Lifting these booleans here avoids prop-drilling them
 * across sibling components (v4: the chat header renders the expand button
 * for a sidebar it isn't a parent of).
 */
import { create } from 'zustand';

const SIDEBAR_COLLAPSED_KEY = 'sidebar_collapsed_v1';
const INTERIM_NARRATION_KEY = 'interim_narration_v1';

function readInitialCollapsed(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1';
  } catch {
    return false;
  }
}

/** Default ON: the narration is the agent telling you what it is about to do,
 *  which is signal. Only an explicit '0' turns it off, so a fresh profile and
 *  an unavailable localStorage both land on the default. */
function readInitialInterimNarration(): boolean {
  if (typeof window === 'undefined') return true;
  try {
    return window.localStorage.getItem(INTERIM_NARRATION_KEY) !== '0';
  } catch {
    return true;
  }
}

interface UIState {
  /** Mobile (< md) agent-list drawer open. Ignored on desktop (sidebar in flow). */
  mobileNavOpen: boolean;
  setMobileNavOpen: (open: boolean) => void;
  toggleMobileNav: () => void;

  /** Desktop (≥ md) sidebar collapse — v4 hides the whole aside; the expand
   *  button lives in the chat header / a floating chip on sub-pages, which is
   *  why this state is shared instead of Sidebar-local. Persisted. */
  sidebarCollapsed: boolean;
  setSidebarCollapsed: (collapsed: boolean) => void;
  toggleSidebar: () => void;

  /** Render NexusPower's own narration at the "progress" tier (legible,
   *  always open) instead of letting it recede like provider chain-of-thought.
   *  Purely a display preference — the frames are identical either way, only
   *  the tier changes, so turning it off restores the pre-A′ look and loses no
   *  content. Kept client-side (localStorage, default on) on purpose: what it
   *  governs is whether the reader finds it noisy, which is the reader's call,
   *  not a platform risk needing a backend flag. Persisted. */
  interimNarration: boolean;
  setInterimNarration: (on: boolean) => void;

  /** ⌘K command palette. The palette element is hosted once in MainLayout;
   *  triggers live in the sidebar (Chats search) and the mobile top strip. */
  paletteOpen: boolean;
  setPaletteOpen: (open: boolean) => void;

  /** A context panel (awareness/jobs/…) requested from the command palette.
   *  On mobile the right bookmark strip is hidden, so ⌘K is the entry point —
   *  it sets this AtomicTabId, ChatView opens the matching drawer and clears it.
   *  Typed as string to keep this store free of component imports. */
  pendingPanel: string | null;
  /**
   * How the pending request should resolve.
   *
   * 'toggle' — the user clicked a panel entry: re-requesting the panel that
   *   is already open closes the drawer (the long-standing behaviour).
   * 'open'   — the caller needs the panel to END UP open, e.g. the creation
   *   studio revealing its configuration panel. Using 'toggle' there is a
   *   bug: entering the studio twice would close the panel the second time.
   */
  pendingPanelMode: 'toggle' | 'open';
  requestPanel: (tab: string) => void;
  /** Ensure a panel is open. Never closes it. */
  openPanel: (tab: string) => void;
  clearPendingPanel: () => void;
}

export const useUIStore = create<UIState>((set) => ({
  mobileNavOpen: false,
  setMobileNavOpen: (open) => set({ mobileNavOpen: open }),
  toggleMobileNav: () => set((s) => ({ mobileNavOpen: !s.mobileNavOpen })),

  sidebarCollapsed: readInitialCollapsed(),
  setSidebarCollapsed: (collapsed) => {
    try {
      window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? '1' : '0');
    } catch { /* storage unavailable — collapse just won't persist */ }
    set({ sidebarCollapsed: collapsed });
  },
  toggleSidebar: () =>
    set((s) => {
      const next = !s.sidebarCollapsed;
      try {
        window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, next ? '1' : '0');
      } catch { /* non-fatal */ }
      return { sidebarCollapsed: next };
    }),

  interimNarration: readInitialInterimNarration(),
  setInterimNarration: (on) => {
    try {
      window.localStorage.setItem(INTERIM_NARRATION_KEY, on ? '1' : '0');
    } catch { /* storage unavailable — the preference just won't persist */ }
    set({ interimNarration: on });
  },

  paletteOpen: false,
  setPaletteOpen: (open) => set({ paletteOpen: open }),

  pendingPanel: null,
  pendingPanelMode: 'toggle',
  requestPanel: (tab) => set({ pendingPanel: tab, pendingPanelMode: 'toggle' }),
  openPanel: (tab) => set({ pendingPanel: tab, pendingPanelMode: 'open' }),
  clearPendingPanel: () => set({ pendingPanel: null, pendingPanelMode: 'toggle' }),
}));
