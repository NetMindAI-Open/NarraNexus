import { useEffect, useRef } from 'react';

/**
 * Dismiss a popover when the user interacts anywhere outside it, or presses
 * Escape.
 *
 * Why a document-level listener and not a full-screen backdrop `<div>`:
 * `position: fixed` resolves against the nearest transformed ancestor, so a
 * backdrop rendered inside an animated row (any element keeping a transform,
 * e.g. `animate-slide-up` with fill: forwards) only covers that row — clicks
 * elsewhere on the page never reach it and the popover appears un-closable.
 * A capture-phase pointerdown listener has no such trap and also survives
 * `stopPropagation` in intermediate handlers.
 *
 * Attach the returned ref to the element that contains BOTH the trigger and
 * the popover panel; interactions inside it are ignored. For portal-shaped
 * popovers whose trigger and panel live in different subtrees, pass the
 * extra elements' refs via `extraRefs` — a target inside ANY of them is
 * treated as inside.
 *
 * Escape dismisses. Scope honestly stated: the listener is document-level
 * bubble phase and stopPropagation only shields WINDOW-level Escape
 * handlers (nm/modal) — other document-level handlers (Dialog,
 * SettingsModal, ArtifactZoomModal, BookmarkDrawer) still fire, so a
 * popover stacked inside one of those closes together with it — unless the
 * outer layer owns the popover's open state and skips its own Escape while
 * it is open (BookmarkDrawer does this for its title switcher only). Making
 * "topmost wins" true across the app needs a shared popover stack, not a
 * per-hook flag. Clicks that land inside a cross-origin iframe never reach
 * the document, so a window `blur` with focus moving to an IFRAME
 * dismisses too (switching tabs/apps blurs without focusing an iframe and
 * correctly keeps the popover).
 */
export function useDismissOnOutside<T extends HTMLElement>(
  active: boolean,
  onDismiss: () => void,
  extraRefs?: ReadonlyArray<React.RefObject<HTMLElement | null>>,
): React.RefObject<T | null> {
  const ref = useRef<T>(null);
  // Latest-value refs so inline callers don't force the listeners to
  // re-subscribe on every render while the popover is open.
  const dismissRef = useRef(onDismiss);
  const extraRefsRef = useRef(extraRefs);
  useEffect(() => {
    dismissRef.current = onDismiss;
    extraRefsRef.current = extraRefs;
  });

  useEffect(() => {
    if (!active) return;
    if (import.meta.env.DEV && !ref.current && !extraRefsRef.current?.some((r) => r.current)) {
      // A forgotten ref reproduces the exact bug this hook exists to fix
      // (an un-closable popover) — fail loudly in development.
      console.warn('useDismissOnOutside: no ref attached while active');
    }
    const isInside = (target: Node | null): boolean => {
      if (!target) return false;
      if (ref.current?.contains(target)) return true;
      return extraRefsRef.current?.some((r) => r.current?.contains(target)) ?? false;
    };
    const onPointerDown = (e: PointerEvent) => {
      if (!isInside(e.target as Node)) dismissRef.current();
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        dismissRef.current();
      }
    };
    const onWindowBlur = () => {
      // Focus moved into an iframe (whose clicks we cannot observe) —
      // dismiss. A tab/app switch blurs without focusing an iframe.
      if (document.activeElement?.tagName === 'IFRAME') dismissRef.current();
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown);
    window.addEventListener('blur', onWindowBlur);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('blur', onWindowBlur);
    };
  }, [active]);

  return ref;
}
