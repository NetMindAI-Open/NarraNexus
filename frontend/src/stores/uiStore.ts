/**
 * @file_name: uiStore.ts
 * @date: 2026-06-22
 * @description: Small UI-only store for layout chrome state that several
 * components share but no backend cares about.
 *
 * Currently: the mobile navigation drawer. On < lg the agent-list sidebar is
 * an off-canvas overlay; the TopBar hamburger toggles it, MainLayout renders
 * its backdrop, and Sidebar closes it on navigation. Lifting that one boolean
 * here avoids prop-drilling it across three sibling components.
 */
import { create } from 'zustand';

interface UIState {
  /** Mobile (< md) agent-list drawer open. Ignored on desktop (sidebar in flow). */
  mobileNavOpen: boolean;
  setMobileNavOpen: (open: boolean) => void;
  toggleMobileNav: () => void;

  /** A context panel (awareness/jobs/…) requested from the command palette.
   *  On mobile the right bookmark strip is hidden, so ⌘K is the entry point —
   *  it sets this AtomicTabId, ChatView opens the matching drawer and clears it.
   *  Typed as string to keep this store free of component imports. */
  pendingPanel: string | null;
  requestPanel: (tab: string) => void;
  clearPendingPanel: () => void;
}

export const useUIStore = create<UIState>((set) => ({
  mobileNavOpen: false,
  setMobileNavOpen: (open) => set({ mobileNavOpen: open }),
  toggleMobileNav: () => set((s) => ({ mobileNavOpen: !s.mobileNavOpen })),

  pendingPanel: null,
  requestPanel: (tab) => set({ pendingPanel: tab }),
  clearPendingPanel: () => set({ pendingPanel: null }),
}));
