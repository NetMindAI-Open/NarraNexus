/**
 * @file_name: useArtifactEditor.ts
 * @author: NetMind.AI
 * @date: 2026-08-19
 * @description: State machine of a resident editing surface (spec A §3/§4):
 * load raw bytes → edit (dirty) → explicit save with the base_hash optimistic
 * lock → 409 conflict two-choice (overwrite / discard).
 *
 * Two loss-prevention rules live here, both of the form "the user's
 * keystrokes never silently vanish":
 *  - a DIRTY editor ignores external reloads (the agent updated the file →
 *    updated_at bumps → the raw url re-mints): the user wins locally, the
 *    divergence surfaces as a 409 at save time where it can be decided;
 *  - unsaved text is mirrored into localStorage and restored on remount —
 *    but only when the on-disk base still matches; a draft over a file that
 *    changed underneath cannot be merged mechanically and is dropped.
 *
 * The lock base is the sha256 of the bytes the editor LOADED, never the
 * table's fingerprint — disk is truth and may have moved past the table.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import type { Artifact } from '@/types/artifact';
import { artifactsApi, ArtifactEditConflictError } from '@/services/artifactsApi';
import { sha256Hex } from '@/lib/sha256';
import { readDraft, sweepStaleDrafts, writeDraft } from '@/lib/artifactEditing/drafts';

export interface ArtifactEditorState {
  status: 'loading' | 'ready' | 'error';
  error: string | null;
  text: string;
  dirty: boolean;
  saving: boolean;
  /** Set after a 409: the sha256 of what is on disk now. */
  conflict: { currentHash: string } | null;
  /** True when this mount restored unsaved text from a local draft. */
  draftRestored: boolean;
  /** True when unsaved text CANNOT be mirrored to localStorage (too large /
      quota) — the surface must tell the user to save promptly instead of
      letting the "draft protects you" promise silently lapse. */
  draftUnavailable: boolean;
  setText: (t: string) => void;
  save: () => Promise<void>;
  /** Conflict choice A: my text wins — resend based on the on-disk hash. */
  overwriteConflict: () => Promise<void>;
  /** Conflict choice B: disk wins — reload and drop the local text. */
  discardConflict: () => Promise<void>;
}

export function useArtifactEditor(artifact: Artifact, url: string | null): ArtifactEditorState {
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [text, setTextState] = useState('');
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState<{ currentHash: string } | null>(null);
  const [draftRestored, setDraftRestored] = useState(false);
  const [draftUnavailable, setDraftUnavailable] = useState(false);

  // The lock base: hash of the bytes the editor loaded (or last saved).
  const baseHashRef = useRef<string>('');
  // Read by the load effect without being a dependency: a url change must
  // NOT re-run the load just because dirtiness changed. Assigned in an
  // effect (post-render), never during render.
  const dirtyRef = useRef(false);
  const textRef = useRef('');
  useEffect(() => {
    dirtyRef.current = dirty;
    textRef.current = text;
  });

  const loadFrom = useCallback(async (rawUrl: string): Promise<void> => {
    const r = await fetch(rawUrl);
    if (!r.ok) throw new Error(`fetch failed: ${r.status}`);
    const buf = await r.arrayBuffer();
    const hash = await sha256Hex(buf);
    // fatal: a document we cannot decode losslessly must not enter an
    // editing surface — a lossy decode would be silently written back on
    // the first save (review #334 I4). The thrown error surfaces as the
    // editor's load-error state (read-only path).
    const loaded = new TextDecoder('utf-8', { fatal: true }).decode(buf);
    baseHashRef.current = hash;

    // Sweep BEFORE restore — ordered by control flow, not by effect
    // declaration order (review #334 r3 M2). Idempotent per session.
    sweepStaleDrafts();
    const draft = readDraft(artifact.artifact_id);
    if (draft && draft.baseHash === hash) {
      // Unsaved work from a previous mount over the SAME base: restore it.
      setTextState(draft.text);
      setDirty(true);
      setDraftRestored(true);
    } else {
      if (draft) writeDraft(artifact.artifact_id, null); // stale base → unmergeable
      setTextState(loaded);
      setDirty(false);
      setDraftRestored(false);
    }
    setConflict(null);
  }, [artifact.artifact_id]);

  useEffect(() => {
    if (!url) return;
    if (dirtyRef.current) return; // user wins locally; divergence → 409 at save
    let cancelled = false;
    (async () => {
      setError(null);
      try {
        await loadFrom(url);
        if (!cancelled) setStatus('ready');
      } catch (e) {
        if (!cancelled) {
          setError(String(e));
          setStatus('error');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [url, loadFrom]);

  const setText = useCallback((t: string) => {
    setTextState(t);
    setDirty(true);
    const persisted = writeDraft(artifact.artifact_id, {
      text: t,
      baseHash: baseHashRef.current,
      ts: Date.now(),
    });
    setDraftUnavailable(!persisted);
  }, [artifact.artifact_id]);

  // Keystrokes must survive a window close even before any save.
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [dirty]);

  const doSave = useCallback(async (baseHash: string) => {
    setSaving(true);
    try {
      const currentText = textRef.current;
      const updated = await artifactsApi.putContent(artifact.agent_id, artifact.artifact_id, {
        content: currentText,
        base_hash: baseHash,
      });
      baseHashRef.current = updated.content_hash ?? (await sha256Hex(currentText));
      setDirty(false);
      setConflict(null);
      writeDraft(artifact.artifact_id, null);
    } catch (e) {
      if (e instanceof ArtifactEditConflictError) {
        setConflict({ currentHash: e.currentHash });
      } else {
        setError(String(e));
      }
    } finally {
      setSaving(false);
    }
  }, [artifact.agent_id, artifact.artifact_id]);

  const save = useCallback(() => doSave(baseHashRef.current), [doSave]);

  const overwriteConflict = useCallback(async () => {
    if (!conflict) return;
    await doSave(conflict.currentHash);
  }, [conflict, doSave]);

  const discardConflict = useCallback(async () => {
    if (!url) return;
    writeDraft(artifact.artifact_id, null);
    setDirty(false);
    await loadFrom(url);
  }, [url, loadFrom, artifact.artifact_id]);

  return {
    status,
    error,
    text,
    dirty,
    saving,
    conflict,
    draftRestored,
    draftUnavailable,
    setText,
    save,
    overwriteConflict,
    discardConflict,
  };
}
