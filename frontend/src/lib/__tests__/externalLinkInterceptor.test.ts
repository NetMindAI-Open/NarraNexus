/**
 * @file_name: externalLinkInterceptor.test.ts
 * @description: Pin the Tauri external-link click interceptor.
 *
 * Symptom this guards against (TODO 2026-05-27): every `<a target="_blank">`
 * in the dmg is dead — Tauri WKWebview either swallows the click or tries
 * to navigate within the webview itself (CSP blocks the load, no visible
 * error). Users click "Getting started" / provider doc links and nothing
 * happens. The browser default `target="_blank"` is what's misbehaving.
 *
 * Fix: single global capturing-phase click handler on document. In Tauri
 * runtime, intercepts external links and routes via plugin-shell open(),
 * which opens in the OS browser. Browser mode is a no-op so the existing
 * `target="_blank"` default keeps working.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

const openExternalMock = vi.fn(async () => true);
const isTauriMock = vi.fn(() => true);

vi.mock('@/lib/tauri', () => ({
  isTauri: () => isTauriMock(),
  openExternal: (url: string) => openExternalMock(url),
}));

import { installExternalLinkInterceptor } from '../externalLinkInterceptor';

let uninstall: () => void;

beforeEach(() => {
  openExternalMock.mockReset();
  openExternalMock.mockResolvedValue(true);
  isTauriMock.mockReset();
  isTauriMock.mockReturnValue(true);
  document.body.innerHTML = '';
  uninstall = installExternalLinkInterceptor();
});

afterEach(() => {
  uninstall();
});

function clickAnchor(html: string): { event: MouseEvent; anchor: HTMLAnchorElement } {
  document.body.innerHTML = html;
  const anchor = document.body.querySelector('a') as HTMLAnchorElement;
  const event = new MouseEvent('click', { bubbles: true, cancelable: true });
  anchor.dispatchEvent(event);
  return { event, anchor };
}

describe('installExternalLinkInterceptor (Tauri runtime)', () => {
  test('intercepts <a target="_blank" href="https://...">, routes via openExternal, preventDefault', () => {
    const { event } = clickAnchor('<a href="https://example.com" target="_blank">Docs</a>');
    expect(openExternalMock).toHaveBeenCalledWith('https://example.com/');
    expect(event.defaultPrevented).toBe(true);
  });

  test('intercepts http:// too (not only https)', () => {
    clickAnchor('<a href="http://example.com" target="_blank">Old</a>');
    expect(openExternalMock).toHaveBeenCalledWith('http://example.com/');
  });

  test('intercepts mailto: links', () => {
    clickAnchor('<a href="mailto:bin@narra.nexus" target="_blank">Email</a>');
    expect(openExternalMock).toHaveBeenCalledWith('mailto:bin@narra.nexus');
  });

  test('intercepts clicks on nested elements inside the <a> (walks up the DOM)', () => {
    document.body.innerHTML =
      '<a href="https://example.com" target="_blank"><span><b>Click me</b></span></a>';
    const innerB = document.body.querySelector('b') as HTMLElement;
    const event = new MouseEvent('click', { bubbles: true, cancelable: true });
    innerB.dispatchEvent(event);
    expect(openExternalMock).toHaveBeenCalledWith('https://example.com/');
    expect(event.defaultPrevented).toBe(true);
  });

  test('does NOT intercept <a> WITHOUT target="_blank" (in-app routing untouched)', () => {
    const { event } = clickAnchor('<a href="https://example.com">Inline</a>');
    expect(openExternalMock).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  test('does NOT intercept disallowed schemes (javascript:, file:, data:)', () => {
    clickAnchor('<a href="javascript:alert(1)" target="_blank">Bad</a>');
    expect(openExternalMock).not.toHaveBeenCalled();
    document.body.innerHTML = '';
    clickAnchor('<a href="file:///etc/passwd" target="_blank">Bad</a>');
    expect(openExternalMock).not.toHaveBeenCalled();
  });

  test('does NOT intercept clicks on plain text (not inside an <a>)', () => {
    document.body.innerHTML = '<span>just text</span>';
    const span = document.body.querySelector('span') as HTMLElement;
    span.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    expect(openExternalMock).not.toHaveBeenCalled();
  });
});

describe('installExternalLinkInterceptor (browser runtime)', () => {
  test('no-op when not in Tauri — uninstaller is safe to call', () => {
    uninstall();
    isTauriMock.mockReturnValue(false);
    uninstall = installExternalLinkInterceptor();
    clickAnchor('<a href="https://example.com" target="_blank">Browser</a>');
    expect(openExternalMock).not.toHaveBeenCalled();
  });
});
