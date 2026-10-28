/**
 * @file_name: useStudioLifecycle.ts
 * @author: NetMind.AI
 * @date: 2026-09-04
 * @description: The ONE invariant that decides how long the creation studio
 * lives on an agent:
 *
 *     the studio is OPEN on agent X  ⇔  X's Builder panel is what the drawer
 *                                       is currently showing
 *
 * Reconciled here, as one effect, instead of being re-derived at every place
 * the drawer can close. The first cut hung "leave the studio" on the drawer's
 * X button only, and three everyday paths kept the flag alive — ⌘K toggling
 * the same panel shut, switching to another drawer tab, switching to another
 * agent — so every later message kept the builder envelope and the model kept
 * writing config into an agent whose panel was no longer on screen. A fourth
 * path (switch agent, keep the drawer) produced a drawer titled "Builder" with
 * nothing inside. Any future way of closing the drawer (Esc, a swipe, a deep
 * link) is covered by construction, because none of them are enumerated here.
 *
 * Two consequences of the invariant, both deliberate and both reversible:
 *   - switching to another drawer tab counts as leaving. The Builder tab
 *     stays on offer (the agent is `visited`), so coming back is one click and
 *     the recommendations are still there. The alternative — keep the studio
 *     alive while another tab shows — would let model writes happen with no
 *     panel to surface their errors, which is the silence this whole round
 *     was about.
 *   - selecting the Builder tab on a resumable agent RESUMES the studio, and
 *     selecting it on an agent that never entered the studio is corrected to
 *     "no tab" so the drawer never opens on an empty panel.
 *
 * Collapsing is `closeStudio` (keeps it resumable); only the panel's Done
 * calls `finishStudio`. Neither the drawer nor this hook ends a studio — and
 * conversely Done does not touch the drawer: after `finishStudio` the agent
 * is neither open nor resumable, so the "drop the tab" branch below collapses
 * the drawer. Done asking the drawer to toggle as well used to race this
 * effect inside one React commit and re-open the drawer on an empty panel.
 */
import { useEffect, useRef } from 'react';
import type { AtomicTabId } from '@/components/bookmarks';
import { useStudioStore, selectStudioOpen, selectStudioResumable, isStudioOpen } from '@/stores/studioStore';

export interface StudioLifecycleInput {
  agentId: string | null | undefined;
  drawerTab: AtomicTabId | null;
  setDrawerTab: (tab: AtomicTabId | null) => void;
}

export function useStudioLifecycle({ agentId, drawerTab, setDrawerTab }: StudioLifecycleInput) {
  const studioOpen = useStudioStore(selectStudioOpen(agentId));
  const studioResumable = useStudioStore(selectStudioResumable(agentId));
  const openStudio = useStudioStore((s) => s.openStudio);
  const closeStudio = useStudioStore((s) => s.closeStudio);
  // The agent whose Builder panel was showing on the previous pass.
  const shownRef = useRef<string | null>(null);

  useEffect(() => {
    const onBuilderTab = drawerTab === 'builder' && !!agentId;
    if (onBuilderTab && !studioOpen) {
      // The tab was picked (⋯ menu / ⌘K / a restored drawerTab) for an
      // agent whose studio is not open: resume it, or drop the tab.
      if (studioResumable) openStudio(agentId);
      else setDrawerTab(null);
    }
    const shown = onBuilderTab && studioOpen ? agentId : null;
    const prev = shownRef.current;
    // `prev` is null on the first pass — nothing to collapse then. A `prev`
    // that is already shut (Done ran `finishStudio`) needs no collapse either.
    if (prev && prev !== shown && isStudioOpen(prev)) closeStudio(prev);
    shownRef.current = shown;
  }, [agentId, drawerTab, studioOpen, studioResumable, openStudio, closeStudio, setDrawerTab]);

  return { studioOpen, studioResumable };
}
