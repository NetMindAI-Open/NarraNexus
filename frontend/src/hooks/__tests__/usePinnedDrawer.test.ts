/**
 * The load-bearing contract: the drawer width is persisted ONLY on the
 * explicit drag release. The stored key doubles as an "existing user"
 * signal for the first-run coach, so a mere mount (or an in-flight drag)
 * writing it would silently burn new users' onboarding.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { usePinnedDrawer } from '../usePinnedDrawer';
import {
  DRAWER_PINNED_KEY,
  DRAWER_WIDTH_KEY,
} from '@/components/layout/drawerLayout';

describe('usePinnedDrawer', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('mounting writes nothing', () => {
    renderHook(() => usePinnedDrawer());
    expect(window.localStorage.getItem(DRAWER_WIDTH_KEY)).toBeNull();
    expect(window.localStorage.getItem(DRAWER_PINNED_KEY)).toBeNull();
  });

  it('an in-flight drag moves the DOM but writes nothing; the release persists the dragged value', () => {
    const { result } = renderHook(() => usePinnedDrawer());
    // A real column element with stubbed geometry — without it both
    // handlers take the colRef-null early-return and the test would pass
    // no matter where the persistence call lived.
    const el = document.createElement('div');
    el.getBoundingClientRect = () => ({ right: 900 } as DOMRect);
    result.current.colRef.current = el;

    // jsdom innerWidth is 1024 → clamp bounds [300, 352]. clientX 560
    // gives 900-560=340: inside the bounds, and distinct from the 400
    // default — so the assertion can tell "computed" from "fell back".
    act(() => result.current.handleResize(560));
    expect(el.style.width).toBe('340px');
    expect(window.localStorage.getItem(DRAWER_WIDTH_KEY)).toBeNull();

    act(() => result.current.handleResizeEnd(560));
    expect(window.localStorage.getItem(DRAWER_WIDTH_KEY)).toBe('340');
  });

  it('pin choices persist and read back', () => {
    const { result } = renderHook(() => usePinnedDrawer());
    expect(result.current.pinned).toBe(true);
    act(() => result.current.setPinned(false));
    expect(window.localStorage.getItem(DRAWER_PINNED_KEY)).toBe('0');
    const { result: second } = renderHook(() => usePinnedDrawer());
    expect(second.current.pinned).toBe(false);
  });
});
