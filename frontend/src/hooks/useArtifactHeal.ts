/**
 * @file_name: useArtifactHeal.ts
 * @description: Shared self-heal flow for artifact renderers.
 *
 * Used by ChartRenderer / HtmlRenderer / CsvRenderer / etc. when their raw
 * fetch returns 410 (file_path is null in the DB or the file is missing on
 * disk). The hook drives the round-trip with the backend `/heal` endpoint:
 *
 *   1. The renderer calls `attempt()` with no args.
 *   2. We POST /heal — server runs the workspace-scan heuristic. If it
 *      finds a unique match it auto-registers and returns recovered=true;
 *      the hook bumps `recoveryVersion` so the renderer's `useEffect`
 *      reruns and fetches the now-valid pointer.
 *   3. Otherwise the hook stashes the candidates and the user sees
 *      `<ArtifactHealModal>` (the renderer renders it conditionally).
 *   4. The user picks one. The renderer calls `attempt(workspacePath)`
 *      and the same flow repeats — server registers onto the picked path,
 *      hook bumps `recoveryVersion`, renderer re-fetches.
 *
 * The renderer is responsible for:
 *   - calling `attempt()` from its fetch-error catch block (status 410)
 *   - re-running its load effect whenever `recoveryVersion` changes
 *   - rendering `<ArtifactHealModal>` driven by hook state
 *
 * The hook does NOT eat ordinary fetch errors — only the renderer knows
 * what status code came back. It just owns the modal state machine.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { artifactsApi, type HealCandidate } from '@/services/artifactsApi';
import { useArtifactStore } from '@/stores';

export interface ArtifactHealController {
  /** Bumped by `attempt()` on successful recovery — use as a useEffect dep
   *  so the renderer reloads the artifact data. */
  recoveryVersion: number;

  /** True while a /heal request is in flight. */
  busy: boolean;

  /** True iff `<ArtifactHealModal>` should be open. Goes true when
   *  /heal needs the user to pick a candidate (0 or multiple matches). */
  modalOpen: boolean;

  /** Candidates to surface in the modal. Empty array is fine — the modal
   *  renders a "no matches" state. */
  candidates: HealCandidate[];

  /** Human-readable result/diagnostic from the server. */
  message: string;

  /**
   * Trigger one heal attempt. With no arg, the server runs its scan
   * heuristic. With a workspace path, the server re-registers onto that
   * path (the "user picked from the modal" flow).
   *
   * Idempotent: while `busy` is true subsequent calls are no-ops.
   */
  attempt: (workspacePath?: string) => Promise<void>;

  /** Close the modal without picking — leaves the broken pointer alone. */
  dismiss: () => void;
}

export function useArtifactHeal(
  agentId: string,
  artifactId: string,
): ArtifactHealController {
  const [recoveryVersion, setRecoveryVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [candidates, setCandidates] = useState<HealCandidate[]>([]);
  const [message, setMessage] = useState('');
  const upsert = useArtifactStore((s) => s.upsert);

  // Dismissed-for-this-artifact latch. Renderer useEffects can fire
  // multiple times against the same broken pointer (URL re-mints, prop
  // churn, etc.) — once the user has explicitly dismissed the "no match"
  // modal, suppress further auto-opens so they aren't trapped in a
  // loop. Resets when artifactId changes (different artifact, fresh
  // chance to heal). See useArtifactHeal.test.tsx for the contract.
  const dismissedRef = useRef(false);
  useEffect(() => {
    dismissedRef.current = false;
  }, [agentId, artifactId]);

  // busyRef lets attempt() guard re-entry without re-creating the
  // callback on every render — keeping the callback stable is what lets
  // the returned controller object below be referentially stable.
  const busyRef = useRef(false);
  busyRef.current = busy;

  const attempt = useCallback(
    async (workspacePath?: string) => {
      if (busyRef.current) return;
      busyRef.current = true;
      setBusy(true);
      try {
        const res = await artifactsApi.heal(agentId, artifactId, workspacePath);
        setMessage(res.message);
        if (res.recovered) {
          // Server already wrote the new pointer. Refresh the in-memory
          // artifact so the next render sees the new size/file_path, and
          // bump the version so the renderer re-fetches the raw bytes.
          if (res.artifact) upsert(res.artifact);
          setModalOpen(false);
          setCandidates([]);
          setRecoveryVersion((v) => v + 1);
        } else {
          setCandidates(res.candidates);
          if (!dismissedRef.current) setModalOpen(true);
        }
      } catch (e) {
        setMessage(`Heal request failed: ${e}`);
        setCandidates([]);
        if (!dismissedRef.current) setModalOpen(true);
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    },
    [agentId, artifactId, upsert],
  );

  const dismiss = useCallback(() => {
    dismissedRef.current = true;
    setModalOpen(false);
  }, []);

  // Memoize the controller so consumer useEffect deps like [url, heal]
  // don't churn on every render. Without this the renderer's HEAD-410
  // → attempt → setState cycle re-creates `heal`, the effect re-fires,
  // and the modal can re-open immediately after dismiss.
  return useMemo(
    () => ({
      recoveryVersion,
      busy,
      modalOpen,
      candidates,
      message,
      attempt,
      dismiss,
    }),
    [recoveryVersion, busy, modalOpen, candidates, message, attempt, dismiss],
  );
}
