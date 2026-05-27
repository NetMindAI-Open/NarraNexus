/**
 * @file_name: externalLinkInterceptor.ts
 * @description: Make `<a target="_blank">` actually open in the OS browser
 * when running inside the Tauri WKWebView.
 *
 * Why this exists: under tauri.localhost, `target="_blank"` does NOT open
 * a new system-browser window like it does in a regular browser. The
 * webview either swallows the click or tries to navigate inside the
 * webview itself (CSP / origin blocks it, no error surfaces). Result:
 * every help link in the dmg ("Getting started", provider docs, channel
 * setup hints, artifact fallback link) is dead. TODO 2026-05-27.
 *
 * Fix: a SINGLE capturing-phase click listener on document. Walks up
 * from the click target to the nearest `<a>`, gates on target="_blank"
 * + a safe URL scheme, then routes via `openExternal` (which uses
 * plugin-shell on the Tauri side). No per-link refactor — every new
 * external link added in the future is automatically covered.
 *
 * Browser mode is a no-op (`isTauri()` returns false) so the default
 * `target="_blank"` behavior is left untouched.
 */

import { isTauri, openExternal } from './tauri';

const ALLOWED_SCHEMES = new Set(['http:', 'https:', 'mailto:', 'tel:']);

function findAnchor(target: EventTarget | null): HTMLAnchorElement | null {
  let node = target as Node | null;
  while (node && node.nodeType !== Node.ELEMENT_NODE) {
    node = node.parentNode;
  }
  let el = node as HTMLElement | null;
  while (el) {
    if (el.tagName === 'A') return el as HTMLAnchorElement;
    el = el.parentElement;
  }
  return null;
}

function isSafeExternalUrl(href: string): boolean {
  try {
    const url = new URL(href);
    return ALLOWED_SCHEMES.has(url.protocol);
  } catch {
    return false;
  }
}

/**
 * Install the interceptor. Returns an unsubscriber for tests / HMR.
 * In browser mode it's a no-op that returns a no-op uninstaller.
 *
 * Idempotent at the listener level — `addEventListener` dedupes identical
 * (function, capture) registrations, so calling install twice in HMR is
 * safe.
 */
export function installExternalLinkInterceptor(): () => void {
  if (!isTauri()) return () => {};

  const handler = (e: MouseEvent) => {
    // Honor modifier-click intents — the user explicitly asked for new
    // tab / new window / save-target. Default browser handling there is
    // already fine; intercepting would be presumptuous.
    if (e.defaultPrevented || e.button !== 0) return;

    const anchor = findAnchor(e.target);
    if (!anchor) return;
    if (anchor.target !== '_blank') return;

    const href = anchor.href;
    if (!href || !isSafeExternalUrl(href)) return;

    e.preventDefault();
    openExternal(href).catch((err) => {
      console.error('[externalLinkInterceptor] open failed:', err);
    });
  };

  document.addEventListener('click', handler, true);
  return () => document.removeEventListener('click', handler, true);
}
