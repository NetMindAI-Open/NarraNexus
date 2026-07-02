/**
 * @file_name: useMediaQuery.ts
 * @date: 2026-06-22
 * @description: Subscribe to a CSS media query from JS. Used where the layout
 * needs to *branch render* (not just toggle CSS) between mobile and desktop —
 * e.g. the chat/artifacts split becomes a tab switcher on phones.
 *
 * The `md` breakpoint (767.98px) matches the Tailwind `md` used across the
 * responsive layout, so JS and CSS agree on where "mobile" ends.
 */
import { useEffect, useState } from 'react';

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window !== 'undefined' ? window.matchMedia(query).matches : false,
  );

  useEffect(() => {
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    onChange();
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, [query]);

  return matches;
}

/** True below the Tailwind `md` breakpoint — the mobile single-column layout. */
export function useIsMobile(): boolean {
  return useMediaQuery('(max-width: 767.98px)');
}
