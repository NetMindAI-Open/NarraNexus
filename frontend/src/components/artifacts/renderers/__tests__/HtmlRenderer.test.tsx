/**
 * @file_name: HtmlRenderer.test.tsx
 * @description: Security contract tests for HtmlRenderer.
 *
 * Asserts the iframe sandbox attribute invariants that prevent agent-emitted
 * HTML from escaping isolation. If a future change relaxes the sandbox
 * policy, this test will fail, surfacing the security regression.
 *
 * NOTE: No test runner (vitest/jest) is currently configured in package.json.
 * This file uses vitest-style imports as documentation-as-code. A future dev
 * who adds @testing-library/react + vitest will find these tests
 * auto-discovered.
 *
 * Pointer model: HtmlRenderer no longer takes a `version` prop and the iframe
 * `src` is set asynchronously from the view-token endpoint. The test stubs
 * `artifactsApi.getRawUrl` so the iframe receives a predictable URL.
 */

import { describe, expect, test, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';

vi.mock('@/services/artifactsApi', () => ({
  artifactsApi: {
    getRawUrl: vi.fn(async () =>
      '/api/public/artifacts/raw/FAKE_TOKEN/',
    ),
  },
}));

import HtmlRenderer from '../HtmlRenderer';
import type { Artifact } from '@/types/artifact';

const fakeArtifact: Artifact = {
  artifact_id: 'art_test1234',
  agent_id: 'agent_x',
  user_id: 'user_y',
  session_id: 's',
  original_session_id: null,
  title: 't',
  kind: 'text/html',
  description: null,
  pinned: false,
  file_path: 'agent_x_user_y/sales_report/index.html',
  size_bytes: 1024,
  created_at: '2026-05-08T00:00:00Z',
  updated_at: '2026-05-08T00:00:00Z',
};

describe('HtmlRenderer security', () => {
  test('sandbox attribute does not allow same-origin or top-navigation', async () => {
    const { container } = render(<HtmlRenderer artifact={fakeArtifact} />);
    const iframe = await waitFor(() => {
      const el = container.querySelector('iframe');
      if (!el) throw new Error('iframe not rendered yet');
      return el;
    });
    const sandbox = (iframe.getAttribute('sandbox') ?? '').split(/\s+/);
    expect(sandbox).toContain('allow-scripts');
    expect(sandbox).not.toContain('allow-same-origin');
    expect(sandbox).not.toContain('allow-top-navigation');
    expect(sandbox).not.toContain('allow-popups-to-escape-sandbox');
  });

  test('src points at the token-protected directory URL', async () => {
    const { container } = render(<HtmlRenderer artifact={fakeArtifact} />);
    const iframe = await waitFor(() => {
      const el = container.querySelector('iframe');
      if (!el) throw new Error('iframe not rendered yet');
      return el;
    });
    expect(iframe.getAttribute('src')).toBe('/api/public/artifacts/raw/FAKE_TOKEN/');
  });
});
