/**
 * @file_name: useArtifactEditor.test.tsx
 * @description: State machine of the resident-editor hook (spec A §3/§4):
 * load → edit (dirty) → save (rebase) / 409 conflict (two-choice), plus the
 * two loss-prevention behaviours — dirty editors ignore external reloads
 * (user wins locally, AionUI dirty-skip parallel) and drafts survive unmount
 * via localStorage.
 */

import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Artifact } from '@/types/artifact';
import { sha256Hex } from '@/lib/sha256';

const putContent = vi.fn();
vi.mock('@/services/artifactsApi', async (importOriginal) => {
  const mod = await importOriginal<typeof import('@/services/artifactsApi')>();
  return {
    ...mod,
    artifactsApi: { ...mod.artifactsApi, putContent: (...a: unknown[]) => putContent(...a) },
  };
});

import { useArtifactEditor } from '../useArtifactEditor';
import { ArtifactEditConflictError } from '@/services/artifactsApi';

const ART: Artifact = {
  artifact_id: 'art_edit1',
  agent_id: 'agent_x',
  user_id: 'user_y',
  session_id: 'sess',
  title: 'notes',
  kind: 'text/csv',
  description: null,
  pinned: false,
  team_id: null,
  file_path: 'ws/notes.csv',
  size_bytes: 6,
  created_at: '2026-08-19T00:00:00Z',
  updated_at: '2026-08-19T00:00:00Z',
};

function mockRawFetch(text: string) {
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

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('useArtifactEditor', () => {
  it('loads the raw content and starts clean', async () => {
    mockRawFetch('a,b\n1,2\n');
    const { result } = renderHook(() => useArtifactEditor(ART, 'http://raw/url/'));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.text).toBe('a,b\n1,2\n');
    expect(result.current.dirty).toBe(false);
  });

  it('setText marks dirty; save sends base_hash of the LOADED bytes and rebases', async () => {
    mockRawFetch('a,b\n');
    const loadedHash = await sha256Hex('a,b\n');
    putContent.mockResolvedValue({ ...ART, content_hash: await sha256Hex('a,b,c\n') });

    const { result } = renderHook(() => useArtifactEditor(ART, 'http://raw/url/'));
    await waitFor(() => expect(result.current.status).toBe('ready'));

    act(() => result.current.setText('a,b,c\n'));
    expect(result.current.dirty).toBe(true);

    await act(() => result.current.save());
    expect(putContent).toHaveBeenCalledWith('agent_x', 'art_edit1', {
      content: 'a,b,c\n',
      base_hash: loadedHash,
    });
    expect(result.current.dirty).toBe(false);
    expect(result.current.conflict).toBeNull();
  });

  it('a 409 surfaces the conflict; overwrite resends with the current hash', async () => {
    mockRawFetch('mine\n');
    putContent
      .mockRejectedValueOnce(new ArtifactEditConflictError('conflict', 'hash-on-disk'))
      .mockResolvedValueOnce({ ...ART, content_hash: 'new-hash' });

    const { result } = renderHook(() => useArtifactEditor(ART, 'http://raw/url/'));
    await waitFor(() => expect(result.current.status).toBe('ready'));

    act(() => result.current.setText('mine edited\n'));
    await act(() => result.current.save());
    expect(result.current.conflict).toEqual({ currentHash: 'hash-on-disk' });
    expect(result.current.dirty).toBe(true); // nothing was lost

    await act(() => result.current.overwriteConflict());
    expect(putContent).toHaveBeenLastCalledWith('agent_x', 'art_edit1', {
      content: 'mine edited\n',
      base_hash: 'hash-on-disk',
    });
    expect(result.current.conflict).toBeNull();
    expect(result.current.dirty).toBe(false);
  });

  it('discarding a conflict reloads from disk and drops the local text', async () => {
    mockRawFetch('theirs\n'); // both initial load and the discard refetch
    putContent.mockRejectedValueOnce(new ArtifactEditConflictError('conflict', 'h'));

    const { result } = renderHook(() => useArtifactEditor(ART, 'http://raw/url/'));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    act(() => result.current.setText('mine\n'));
    await act(() => result.current.save());
    expect(result.current.conflict).not.toBeNull();

    await act(() => result.current.discardConflict());
    expect(result.current.text).toBe('theirs\n');
    expect(result.current.dirty).toBe(false);
    expect(result.current.conflict).toBeNull();
  });

  it('a DIRTY editor ignores an external url change — the user wins locally', async () => {
    mockRawFetch('v1\n');
    const { result, rerender } = renderHook(
      ({ url }) => useArtifactEditor(ART, url),
      { initialProps: { url: 'http://raw/v1/' } },
    );
    await waitFor(() => expect(result.current.status).toBe('ready'));
    act(() => result.current.setText('v1 plus my typing\n'));

    mockRawFetch('v2-from-agent\n');
    rerender({ url: 'http://raw/v2/' });
    // no reload happened: local text intact
    await new Promise((r) => setTimeout(r, 10));
    expect(result.current.text).toBe('v1 plus my typing\n');
    expect(result.current.dirty).toBe(true);
  });

  it('a CLEAN editor follows an external url change', async () => {
    mockRawFetch('v1\n');
    const { result, rerender } = renderHook(
      ({ url }) => useArtifactEditor(ART, url),
      { initialProps: { url: 'http://raw/v1/' } },
    );
    await waitFor(() => expect(result.current.status).toBe('ready'));

    mockRawFetch('v2-from-agent\n');
    rerender({ url: 'http://raw/v2/' });
    await waitFor(() => expect(result.current.text).toBe('v2-from-agent\n'));
    expect(result.current.dirty).toBe(false);
  });

  it('persists a draft and restores it on remount when the base still matches', async () => {
    mockRawFetch('base\n');
    const first = renderHook(() => useArtifactEditor(ART, 'http://raw/url/'));
    await waitFor(() => expect(first.result.current.status).toBe('ready'));
    act(() => first.result.current.setText('base plus unsaved work\n'));
    first.unmount();

    mockRawFetch('base\n'); // disk unchanged
    const second = renderHook(() => useArtifactEditor(ART, 'http://raw/url/'));
    await waitFor(() => expect(second.result.current.status).toBe('ready'));
    expect(second.result.current.text).toBe('base plus unsaved work\n');
    expect(second.result.current.dirty).toBe(true);
    expect(second.result.current.draftRestored).toBe(true);
  });

  it('drops the draft when the file changed underneath it', async () => {
    mockRawFetch('base\n');
    const first = renderHook(() => useArtifactEditor(ART, 'http://raw/url/'));
    await waitFor(() => expect(first.result.current.status).toBe('ready'));
    act(() => first.result.current.setText('unsaved\n'));
    first.unmount();

    mockRawFetch('rewritten by agent\n');
    const second = renderHook(() => useArtifactEditor(ART, 'http://raw/url/'));
    await waitFor(() => expect(second.result.current.status).toBe('ready'));
    expect(second.result.current.text).toBe('rewritten by agent\n');
    expect(second.result.current.dirty).toBe(false);
    expect(second.result.current.draftRestored).toBe(false);
  });

  it('saving clears the stored draft', async () => {
    mockRawFetch('base\n');
    putContent.mockResolvedValue({ ...ART, content_hash: 'h2' });
    const { result } = renderHook(() => useArtifactEditor(ART, 'http://raw/url/'));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    act(() => result.current.setText('changed\n'));
    expect(localStorage.getItem('narra:artifact-draft:art_edit1')).not.toBeNull();
    await act(() => result.current.save());
    expect(localStorage.getItem('narra:artifact-draft:art_edit1')).toBeNull();
  });
});
