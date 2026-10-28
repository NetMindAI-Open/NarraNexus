/**
 * @file_name: usePinnedDrawer.ts
 * @author: NarraNexus
 * @date: 2026-08-19
 * @description: THE pinned-drawer behavior — pin state, user-chosen width,
 * viewport-aware clamping, and the drag plumbing — shared by every surface
 * that mounts a BookmarkDrawer (single chat, team room).
 *
 * One implementation on purpose: the pin preference and the chosen width
 * are one user preference, and "the right column works the same
 * everywhere" is the whole contract. Policy constants and the pure math
 * live in layout/drawerLayout.ts; this hook owns the React wiring.
 *
 * Width is persisted ONLY on the explicit drag release: the stored key
 * doubles as an "existing user" signal for the first-run coach, so it must
 * mean "the user chose a width", never "a view mounted". Corollary of the
 * shared keys: pinning or dragging in the TEAM room also spends that
 * first-run signal — deliberate, the user has demonstrably found the
 * drawer, wherever they found it.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  DEFAULT_DRAWER_PX,
  DRAWER_PINNED_KEY,
  DRAWER_WIDTH_KEY,
  clampDrawerWidth,
  readInitialDrawerPinned,
} from '@/components/layout/drawerLayout';

function readInitialDrawerWidth(): number {
  if (typeof window === 'undefined') return DEFAULT_DRAWER_PX;
  const raw = window.localStorage.getItem(DRAWER_WIDTH_KEY);
  if (!raw) return DEFAULT_DRAWER_PX;
  const parsed = parseFloat(raw);
  if (!Number.isFinite(parsed)) return DEFAULT_DRAWER_PX;
  return clampDrawerWidth(parsed, window.innerWidth);
}

export function usePinnedDrawer() {
  const [pinned, setPinnedState] = useState<boolean>(() => {
    if (typeof window === 'undefined') return true;
    return readInitialDrawerPinned(window.localStorage);
  });
  const setPinned = useCallback((next: boolean) => {
    setPinnedState(next);
    try {
      window.localStorage.setItem(DRAWER_PINNED_KEY, next ? '1' : '0');
    } catch { /* non-fatal */ }
  }, []);

  const [width, setWidth] = useState<number>(() => readInitialDrawerWidth());

  // The width CAP is viewport-relative, so a window shrink (unplugging a
  // monitor) must re-clamp at render time. The STORED width keeps the
  // user's chosen value — clamping state would let one visit to the laptop
  // screen permanently overwrite a deliberate big-screen width.
  const [viewportW, setViewportW] = useState<number>(() =>
    typeof window !== 'undefined' ? window.innerWidth : 1920,
  );
  useEffect(() => {
    // rAF-coalesced: raw resize fires at ~60Hz and a setState per event
    // would reconcile the consuming subtree every frame of a window drag.
    // A debounce would be wrong — late clamping is exactly the overflow
    // this state exists to prevent.
    let raf = 0;
    const onResize = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => setViewportW(window.innerWidth));
    };
    window.addEventListener('resize', onResize);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', onResize);
    };
  }, []);
  const effectiveWidth = clampDrawerWidth(width, viewportW);

  // Drag: two-phase (imperative during, committed + persisted on release).
  // Width grows leftward from the drawer's own right edge, so the edge
  // stays put while the neighbouring column absorbs the change.
  const colRef = useRef<HTMLDivElement | null>(null);
  const pendingWidthRef = useRef<number>(width);

  const computeWidth = useCallback((clientX: number): number | null => {
    const el = colRef.current;
    if (!el) return null;
    const right = el.getBoundingClientRect().right;
    return clampDrawerWidth(right - clientX, window.innerWidth);
  }, []);

  const handleResize = useCallback((clientX: number) => {
    const el = colRef.current;
    const next = computeWidth(clientX);
    if (!el || next === null) return;
    pendingWidthRef.current = next;
    el.style.width = `${next}px`;
  }, [computeWidth]);

  const handleResizeEnd = useCallback((clientX: number) => {
    const next = computeWidth(clientX);
    if (next !== null) pendingWidthRef.current = next;
    setWidth(pendingWidthRef.current);
    // Persist HERE, on the explicit release, and nowhere else (see the
    // file header for why the key must carry intent).
    try {
      window.localStorage.setItem(DRAWER_WIDTH_KEY, String(pendingWidthRef.current));
    } catch { /* non-fatal */ }
  }, [computeWidth]);

  return { pinned, setPinned, effectiveWidth, colRef, handleResize, handleResizeEnd };
}
