/**
 * @file_name: scrollContainment.test.tsx
 * @description: Scroll-ownership contract tests for ArtifactRenderer.
 *
 * The artifact column's content box is overflow-hidden by design (the
 * drag-freeze mechanism in ArtifactColumn relies on clipping), so the
 * dispatcher's bounded wrapper owns all scrolling in the column: h-full
 * pins it to the column's height and overflow-auto hosts both vertical
 * and wide-table horizontal overflow. Renderer roots stay unbounded
 * (auto height) — a bounded renderer root would resolve h-full against
 * the zoom modal's fixed-height scale layers too, clamping content to
 * one screen and nesting scrollbars inside the modal.
 *
 * The zoom modal passes bounded={false}: no wrapper, renderers overflow
 * the scale-sizer layers, and the modal's own overflow-auto container
 * carries all scrolling (its zoom mechanism depends on that overflow).
 *
 * SCOPE: jsdom has no layout engine, so these tests pin classNames and
 * DOM shape only. They cannot verify that the parent chain actually
 * resolves a height for h-full (the original bug's failure mode), nor
 * real scrolling in either surface — that stays a manual check across
 * the five artifact kinds, desktop wheel + mobile touch emulation.
 */

import { describe, expect, test, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';

vi.mock('@/services/artifactsApi', () => ({
  artifactsApi: {
    getRawUrl: vi.fn(async () => '/api/public/artifacts/raw/FAKE_TOKEN/'),
    putContent: vi.fn(),
  },
  ArtifactEditConflictError: class extends Error {},
  fetchArtifactText: vi.fn(async (url: string) =>
    url.includes('csv') ? 'col_a,col_b\n1,2' : '# heading\n\nbody text',
  ),
  fetchArtifactBlobUrl: vi.fn(async () => 'blob:http://tauri.localhost/fake'),
}));

// The csv resident editor loads raw BYTES via global fetch (it hashes them
// for the optimistic lock), not via fetchArtifactText.
vi.stubGlobal('fetch', vi.fn(async () => ({
  ok: true,
  status: 200,
  arrayBuffer: async () => new TextEncoder().encode('col_a,col_b\n1,2').buffer,
})));

vi.mock('@/lib/tauri', async () => {
  const actual = await vi.importActual<typeof import('@/lib/tauri')>('@/lib/tauri');
  return {
    ...actual,
    isTauri: () => false,
    fetchArtifactViaTauri: vi.fn(async () => null),
  };
});

import ArtifactRenderer from '../ArtifactRenderer';
import type { Artifact } from '@/types/artifact';

const markdownArtifact: Artifact = {
  artifact_id: 'art_scroll01',
  agent_id: 'agent_x',
  user_id: 'user_y',
  session_id: 's',
  original_session_id: null,
  title: 't',
  kind: 'text/markdown',
  description: null,
  pinned: false,
  file_path: 'agent_x_user_y/report/report.md',
  size_bytes: 1024,
  created_at: '2026-05-08T00:00:00Z',
  updated_at: '2026-05-08T00:00:00Z',
};

const csvArtifact: Artifact = {
  ...markdownArtifact,
  artifact_id: 'art_scroll02',
  kind: 'text/csv',
  file_path: 'agent_x_user_y/data/table.csv',
};

/**
 * These renders wait on an async artifact load plus a markdown parse, and the
 * default 1s ceiling is a statement about how fast the machine is, not about
 * what the component does. It held while this file ran early in a small suite
 * and started failing intermittently once the suite grew — a flake that teaches
 * people to re-run rather than to read. The assertions below are unchanged.
 */
const RENDER_TIMEOUT = { timeout: 5000 };

describe('ArtifactRenderer scroll ownership', () => {
  test('bounded (default): wrapper is the scroll container hosting the renderer', async () => {
    const { container } = render(<ArtifactRenderer artifact={markdownArtifact} />);
    const content = await waitFor(() => {
      const el = container.querySelector('.markdown-content');
      if (!el) throw new Error('markdown content not rendered yet');
      return el;
    }, RENDER_TIMEOUT);
    const wrapper = container.firstElementChild as Element;
    expect(wrapper.className).toContain('h-full');
    expect(wrapper.className).toContain('w-full');
    expect(wrapper.className).toContain('overflow-auto');
    expect(wrapper.className).toContain('overscroll-contain');
    // Shared with the zoom modal: absolute would escape the scale-sizer.
    expect(wrapper.className).not.toContain('absolute');
    expect(wrapper.contains(content)).toBe(true);
  });

  test('bounded (default): the csv resident editor lives inside the same wrapper', async () => {
    // csv renders as a resident editor since the no-mode framework (2026-08-19);
    // the containment contract is unchanged — the editor surface must sit
    // inside the bounded scroll wrapper, never own page-level overflow.
    const { container } = render(<ArtifactRenderer artifact={csvArtifact} />);
    const editorRoot = await waitFor(() => {
      const el = container.querySelector('.cm-editor');
      if (!el) throw new Error('csv editor not rendered yet');
      return el;
    }, RENDER_TIMEOUT);
    const wrapper = container.firstElementChild as Element;
    expect(wrapper.className).toContain('overflow-auto');
    expect(wrapper.contains(editorRoot)).toBe(true);
  });

  test('renderer roots stay unbounded so the zoom modal keeps outer-scroll semantics', async () => {
    const { container } = render(<ArtifactRenderer artifact={markdownArtifact} />);
    const content = await waitFor(() => {
      const el = container.querySelector('.markdown-content');
      if (!el) throw new Error('markdown content not rendered yet');
      return el;
    }, RENDER_TIMEOUT);
    // A bounded renderer root would resolve against the modal's fixed-height
    // layers and clamp to one screen — the wrapper is the only scroll owner.
    expect(content.className).not.toContain('h-full');
  });

  test('bounded={false} (zoom modal): no scroll wrapper is introduced', async () => {
    const { container } = render(
      <ArtifactRenderer artifact={markdownArtifact} bounded={false} />,
    );
    await waitFor(() => {
      if (!container.querySelector('.markdown-content')) {
        throw new Error('markdown content not rendered yet');
      }
    }, RENDER_TIMEOUT);
    expect(container.querySelector('.overscroll-contain')).toBeNull();
    expect(container.querySelector('.overflow-auto')).toBeNull();
  });
});
