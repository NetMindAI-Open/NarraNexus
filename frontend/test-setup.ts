/**
 * Vitest global setup — runs before every test module's imports.
 *
 * Why this file exists: jsdom (the default vitest environment) does
 * not implement window.matchMedia, but several stores in this repo
 * call it at module-init time (notably themeStore, transitively
 * loaded by Markdown). Without this stub, any test that imports
 * anything that touches the theme system would fail before any
 * test code runs.
 *
 * Also extends vitest's `expect` with @testing-library/jest-dom matchers
 * (toBeInTheDocument, toHaveAttribute, toHaveTextContent, etc.) used by
 * NM primitive component tests in components/nm/__tests__/.
 */
import '@testing-library/jest-dom/vitest';
// Initialize i18next so components using `useTranslation()` resolve real
// strings (English fallback) instead of returning raw keys in tests.
import './src/i18n';
// The shell's registries are populated by main.tsx in the app; tests that
// render the shell need the same builtin registrations.
import './src/platform/builtin';


// Node 22 ships an experimental built-in `localStorage` (gated by
// `--localstorage-file`). When that flag reaches the runner without a valid
// path it installs a BROKEN global that shadows jsdom's — `localStorage.clear`
// is then "not a function", so every test with a `beforeEach(localStorage.clear)`
// fails before running. Install a small in-memory Storage whenever the global
// one is missing or incomplete, so the suite is independent of the Node build.
if (typeof localStorage === 'undefined' || typeof localStorage.clear !== 'function') {
  let store: Record<string, string> = {};
  const mem = {
    get length() { return Object.keys(store).length; },
    clear() { store = {}; },
    getItem(k: string) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    setItem(k: string, v: string) { store[k] = String(v); },
    removeItem(k: string) { delete store[k]; },
    key(i: number) { return Object.keys(store)[i] ?? null; },
  } as Storage;
  const def = { value: mem, configurable: true, writable: true };
  Object.defineProperty(globalThis, 'localStorage', def);
  if (typeof window !== 'undefined') Object.defineProperty(window, 'localStorage', def);
}

// jsdom does not implement scrollIntoView. Any test that renders a transcript
// with at least one message hits the follow-the-bottom effect and throws inside
// a passive effect — which vitest reports as an unhandled error AFTER the test
// has already passed, so the suite is green and noisy at the same time. A no-op
// is the honest stub: what the effect does is unobservable in jsdom either way,
// and the stickiness DECISION (whether it should scroll at all) is unit-tested
// in lib/__tests__/scrollStickiness.test.ts.
if (typeof Element !== 'undefined' && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

// jsdom implements neither IntersectionObserver nor ResizeObserver; the md
// block editor (Milkdown/Crepe) constructs both at mount for code blocks and
// toolbar placement. Observing nothing is honest in jsdom — layout callbacks
// are unobservable there anyway.
class _NoopObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() { return []; }
}
if (typeof globalThis.IntersectionObserver === 'undefined') {
  (globalThis as Record<string, unknown>).IntersectionObserver = _NoopObserver;
}
if (typeof globalThis.ResizeObserver === 'undefined') {
  (globalThis as Record<string, unknown>).ResizeObserver = _NoopObserver;
}

if (typeof window !== 'undefined' && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList;
}
