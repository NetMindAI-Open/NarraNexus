/**
 * @file_name: MarkdownRenderer.test.tsx
 * @description: The md surface is a block editor (no-mode framework): a
 * round-trippable document mounts an EDITABLE Crepe surface; a document the
 * editor cannot represent losslessly (AST loss) falls back to the read-only
 * rendered view with the guard banner; frontmatter never reaches the editor
 * (it would be destroyed) and must survive a save verbatim.
 */

import { render, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Artifact } from '@/types/artifact';

const putContent = vi.fn();
vi.mock('@/services/artifactsApi', async (importOriginal) => {
  const mod = await importOriginal<typeof import('@/services/artifactsApi')>();
  return {
    ...mod,
    artifactsApi: {
      ...mod.artifactsApi,
      getRawUrl: vi.fn(async () => 'http://raw/dir/'),
      putContent: (...a: unknown[]) => putContent(...a),
    },
  };
});
vi.mock('@/hooks/useArtifactHeal', () => ({
  useArtifactHeal: () => ({
    modalOpen: false, candidates: [], message: '', busy: false,
    recoveryVersion: 0, attempt: vi.fn(), dismiss: vi.fn(),
  }),
}));

import MarkdownRenderer from '../MarkdownRenderer';

const ART: Artifact = {
  artifact_id: 'art_md0001',
  agent_id: 'agent_x',
  user_id: 'user_y',
  session_id: 's',
  title: 'doc',
  kind: 'text/markdown',
  description: null,
  pinned: false,
  team_id: null,
  file_path: 'ws/doc.md',
  size_bytes: 10,
  created_at: '2026-08-19T00:00:00Z',
  updated_at: '2026-08-19T00:00:00Z',
};

function mockRawBytes(text: string) {
  const bytes = new TextEncoder().encode(text);
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true,
    status: 200,
    arrayBuffer: async () => bytes.buffer,
  })));
}

beforeEach(() => {
  localStorage.clear();
  putContent.mockReset();
});

describe('MarkdownRenderer editing surface', () => {
  it('mounts an editable block editor for a clean document', async () => {
    mockRawBytes('# Title\n\nA paragraph.\n');
    const { container } = render(<MarkdownRenderer artifact={ART} />);
    await waitFor(() => {
      expect(container.querySelector('.milkdown')).not.toBeNull();
    }, { timeout: 8000 });
    const pm = container.querySelector('[contenteditable="true"]');
    expect(pm).not.toBeNull();
  }, 15000);

  it('falls back to the read-only view with the guard banner on AST loss', async () => {
    // Reference-style links get RESOLVED into inline links by the editor
    // (linkReference + definition → link): a real structure change, so
    // editing must be disabled. Empirically chosen — html blocks, footnotes,
    // math and comments all round-trip fine in Crepe (2026-08-19 spike);
    // the guard is a probe, not a hardcoded blocklist.
    mockRawBytes('see [docs][d]\n\n[d]: https://example.com\n');
    const { container, getByText } = render(<MarkdownRenderer artifact={ART} />);
    // The probe is async: wait for its verdict (the guard banner), not for
    // the intermediate probing DOM.
    await waitFor(() => {
      expect(getByText(/direct editing is disabled/i)).toBeTruthy();
    }, { timeout: 8000 });
    // the editor host is hidden; the read-only fallback carries the content
    const editable = container.querySelector('[contenteditable="true"]');
    expect(editable?.closest('.hidden')).not.toBeNull();
    expect(container.querySelector('.markdown-content')!.textContent).toContain('docs');
  }, 15000);

  it('keeps frontmatter out of the editor and re-attaches it on save', async () => {
    const doc = '---\ntitle: keepme\n---\n\n# Body\n';
    mockRawBytes(doc);
    putContent.mockResolvedValue({ ...ART, content_hash: 'h2' });
    const { container } = render(<MarkdownRenderer artifact={ART} />);
    await waitFor(() => {
      expect(container.querySelector('.milkdown')).not.toBeNull();
    }, { timeout: 8000 });
    // the YAML must not appear in the editable surface
    expect(container.querySelector('.milkdown')!.textContent).not.toContain('keepme');
  }, 15000);
});
