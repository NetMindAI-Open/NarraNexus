/**
 * @file_name: UrlRenderer.test.tsx
 * @description: Behavior + security contract tests for UrlRenderer.
 *
 * Locks two things:
 *   1. iframe mode renders an iframe pointing at the EXTERNAL url with the
 *      documented sandbox tokens. Unlike HtmlRenderer this sandbox DOES include
 *      allow-same-origin — safe only because URL tabs are cross-origin
 *      third-party content (the backend refuses self-origin tabs). If someone
 *      relaxes it further (e.g. allow-top-navigation) this test surfaces it.
 *   2. stream mode renders the fallback card (no iframe), where the future
 *      streaming renderer plugs in.
 */
import { describe, expect, test, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';

import type { UrlArtifactDoc } from '@/types/artifact';

let currentDoc: UrlArtifactDoc;

vi.mock('@/services/artifactsApi', () => ({
  artifactsApi: {
    getRawUrl: vi.fn(async () => '/api/public/artifacts/raw/FAKE/'),
    setEmbedMode: vi.fn(async () => ({})),
  },
  fetchArtifactText: vi.fn(async () => JSON.stringify(currentDoc)),
}));

import UrlRenderer from '../UrlRenderer';
import type { Artifact } from '@/types/artifact';

const artifact: Artifact = {
  artifact_id: 'art_url1',
  agent_id: 'agent_x',
  user_id: 'user_y',
  session_id: null,
  original_session_id: null,
  title: 'Example',
  kind: 'application/x-url',
  description: 'https://example.com',
  pinned: true,
  file_path: 'agent_x_user_y/tabs/abc/page.url.json',
  size_bytes: 100,
  created_at: '2026-07-22T00:00:00Z',
  updated_at: '2026-07-22T00:00:00Z',
};

describe('UrlRenderer', () => {
  test('iframe mode: renders external iframe with the documented sandbox', async () => {
    currentDoc = {
      schema_version: 1,
      url: 'https://example.com',
      title: 'Example',
      embed: { recommended: 'iframe', reason: 'no-blocking-headers', probe_status: 'ok', user_override: null },
    };
    const { container } = render(<UrlRenderer artifact={artifact} />);
    const iframe = await waitFor(() => {
      const el = container.querySelector('iframe');
      if (!el) throw new Error('iframe not rendered yet');
      return el;
    });
    expect(iframe.getAttribute('src')).toBe('https://example.com');
    const sandbox = (iframe.getAttribute('sandbox') ?? '').split(/\s+/);
    expect(sandbox).toContain('allow-scripts');
    expect(sandbox).toContain('allow-same-origin'); // intentional for 3rd-party
    // Popups allowed so in-page target=_blank links WORK (open in browser) —
    // a blocked link is worse than one that opens externally, and iframe can't
    // make it an in-app tab.
    expect(sandbox).toContain('allow-popups');
    // Guard against over-broadening: top-navigation must never be granted
    // (a malicious embedded page could navigate our whole app away).
    expect(sandbox).not.toContain('allow-top-navigation');
    expect(iframe.getAttribute('referrerPolicy')).toBe('no-referrer');
  });

  test('stream mode: renders the fallback card, no iframe', async () => {
    currentDoc = {
      schema_version: 1,
      url: 'https://github.com',
      title: 'GitHub',
      embed: { recommended: 'stream', reason: 'x-frame-options', probe_status: 'ok', user_override: null },
    };
    const { container, findByText } = render(<UrlRenderer artifact={artifact} />);
    await findByText(/refuses to be embedded/i); // unique to the fallback card
    expect(container.querySelector('iframe')).toBeNull();
  });

  test('user override wins: stream recommend + iframe override renders iframe', async () => {
    currentDoc = {
      schema_version: 1,
      url: 'https://github.com',
      title: 'GitHub',
      embed: { recommended: 'stream', reason: 'x-frame-options', probe_status: 'ok', user_override: 'iframe' },
    };
    const { container } = render(<UrlRenderer artifact={artifact} />);
    await waitFor(() => {
      if (!container.querySelector('iframe')) throw new Error('iframe not rendered');
    });
    expect(container.querySelector('iframe')?.getAttribute('src')).toBe('https://github.com');
  });
});
