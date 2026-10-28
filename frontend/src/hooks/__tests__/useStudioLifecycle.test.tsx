/**
 * The studio's lifetime is one invariant — open ⇔ its panel is showing for
 * this agent — and these are the four paths that each used to leak a flag
 * (or an empty drawer) when the rule lived only in the drawer's X button.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useStudioLifecycle } from '../useStudioLifecycle';
import { useStudioStore, isStudioOpen, selectStudioResumable } from '@/stores/studioStore';
import type { AtomicTabId } from '@/components/bookmarks';

const A = 'agent_a';
const B = 'agent_b';

function mount(agentId: string, drawerTab: AtomicTabId | null) {
  const setDrawerTab = vi.fn();
  const hook = renderHook(
    (p: { agentId: string; drawerTab: AtomicTabId | null }) =>
      useStudioLifecycle({ ...p, setDrawerTab }),
    { initialProps: { agentId, drawerTab } },
  );
  return { hook, setDrawerTab };
}

beforeEach(() => {
  window.sessionStorage.clear();
  useStudioStore.setState({ open: {}, visited: {}, recommendations: {}, applyError: {} });
});

describe('useStudioLifecycle', () => {
  it('does nothing on first mount when the panel is already showing', () => {
    useStudioStore.getState().openStudio(A);
    const { hook, setDrawerTab } = mount(A, 'builder');
    expect(isStudioOpen(A)).toBe(true);
    expect(setDrawerTab).not.toHaveBeenCalled();
    expect(hook.result.current.studioOpen).toBe(true);
  });

  it('collapses the studio when the drawer closes by ANY route (⌘K toggle, X, Esc…)', () => {
    useStudioStore.getState().openStudio(A);
    const { hook } = mount(A, 'builder');
    act(() => hook.rerender({ agentId: A, drawerTab: null }));
    expect(isStudioOpen(A)).toBe(false);
    // …but it is resumable: the X was not "end the flow"
    expect(selectStudioResumable(A)(useStudioStore.getState())).toBe(true);
  });

  it('collapses the studio when another drawer tab takes over', () => {
    useStudioStore.getState().openStudio(A);
    const { hook } = mount(A, 'builder');
    act(() => hook.rerender({ agentId: A, drawerTab: 'awareness' }));
    expect(isStudioOpen(A)).toBe(false);
    expect(selectStudioResumable(A)(useStudioStore.getState())).toBe(true);
  });

  it('switching agent collapses the OLD agent, never touches the new one, and drops the empty tab', () => {
    useStudioStore.getState().openStudio(A);
    const { hook, setDrawerTab } = mount(A, 'builder');
    act(() => hook.rerender({ agentId: B, drawerTab: 'builder' }));
    expect(isStudioOpen(A)).toBe(false);
    expect(isStudioOpen(B)).toBe(false);
    expect(selectStudioResumable(B)(useStudioStore.getState())).toBe(false);
    // B never entered the studio: a "Builder" drawer with nothing inside is
    // corrected to no tab at all.
    expect(setDrawerTab).toHaveBeenCalledWith(null);
  });

  it('switching to an agent whose studio was collapsed RESUMES it instead', () => {
    useStudioStore.getState().openStudio(B);
    useStudioStore.getState().closeStudio(B); // collapsed earlier, resumable
    useStudioStore.getState().openStudio(A);
    const { hook, setDrawerTab } = mount(A, 'builder');
    act(() => hook.rerender({ agentId: B, drawerTab: 'builder' }));
    expect(isStudioOpen(A)).toBe(false);
    expect(isStudioOpen(B)).toBe(true);
    expect(setDrawerTab).not.toHaveBeenCalled();
  });

  it('picking the Builder tab on a resumable agent resumes; on a stranger it is refused', () => {
    useStudioStore.getState().openStudio(A);
    useStudioStore.getState().closeStudio(A);
    const { hook, setDrawerTab } = mount(A, null);
    act(() => hook.rerender({ agentId: A, drawerTab: 'builder' }));
    expect(isStudioOpen(A)).toBe(true);
    expect(setDrawerTab).not.toHaveBeenCalled();

    const other = mount(B, 'builder');
    expect(isStudioOpen(B)).toBe(false);
    expect(other.setDrawerTab).toHaveBeenCalledWith(null);
  });

  it('after Done (finishStudio) the drawer collapses — the panel does not have to ask', () => {
    // Regression: Done also requested a drawer toggle; batched with the
    // finish into one commit, the toggle read the tab this hook had just
    // dropped as "not open" and re-opened the drawer on an empty panel.
    useStudioStore.getState().openStudio(A);
    const { hook, setDrawerTab } = mount(A, 'builder');
    act(() => useStudioStore.getState().finishStudio(A));
    // drawerTab is still 'builder' in this pass — nothing else changed it.
    hook.rerender({ agentId: A, drawerTab: 'builder' });
    expect(setDrawerTab).toHaveBeenCalledWith(null);
    expect(isStudioOpen(A)).toBe(false);
    expect(selectStudioResumable(A)(useStudioStore.getState())).toBe(false);
  });

  it('closing the drawer on another agent does not collapse a studio elsewhere', () => {
    useStudioStore.getState().openStudio(A);
    const { hook } = mount(B, 'awareness');
    act(() => hook.rerender({ agentId: B, drawerTab: null }));
    expect(isStudioOpen(A)).toBe(true);
  });
});
