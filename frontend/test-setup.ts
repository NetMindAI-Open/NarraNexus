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
