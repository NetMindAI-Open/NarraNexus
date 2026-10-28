/**
 * @file_name: ChartRenderer.tsx
 * @description: Lazy-loaded renderer for application/vnd.echarts+json artifacts.
 *
 * ECharts (~700 KB) is loaded via dynamic import() so it is never included in
 * the initial bundle. The import fires only when this component mounts, which
 * only happens when the user opens an application/vnd.echarts+json tab.
 *
 * The JSON artifact content must be a valid ECharts `option` object
 * (https://echarts.apache.org/en/option.html). The agent is responsible for
 * emitting well-formed option JSON; this renderer makes no attempt to
 * validate or repair the payload.
 *
 * Pointer model: JSON is fetched from a token-protected public URL minted
 * via `useArtifactRawUrl`. No auth header is needed.
 *
 * Cleanup: the `disposed` flag guards against setState-after-unmount races
 * and `chart.dispose()` is called in the cleanup function to release canvas.
 */

import { useEffect, useRef, useState } from 'react';
import type { Artifact } from '@/types/artifact';
import { fetchArtifactText } from '@/services/artifactsApi';
import { useArtifactStore, type ChartInstanceLike } from '@/stores/artifactStore';
import { useArtifactRawUrl } from '@/hooks/useArtifactRawUrl';
import { useArtifactHeal } from '@/hooks/useArtifactHeal';
import { pickNMTheme } from '@/lib/echarts-nm-theme';
import ArtifactHealModal from '../ArtifactHealModal';

interface Props {
  artifact: Artifact;
}

export default function ChartRenderer({ artifact }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { url, error: urlError, reload } = useArtifactRawUrl(
    artifact.agent_id,
    artifact.artifact_id,
    artifact.updated_at,
  );
  const registerChartInstance = useArtifactStore((s) => s.registerChartInstance);
  const unregisterChartInstance = useArtifactStore((s) => s.unregisterChartInstance);
  const heal = useArtifactHeal(artifact.agent_id, artifact.artifact_id);
  // Stash attempt() in a ref so the load effect only re-runs when URL
  // changes — pulling `heal` into the deps would re-fire the fetch (and
  // open the modal again) on every hook state change. Bug: 2026-05-25.
  const attemptRef = useRef(heal.attempt);
  useEffect(() => {
    attemptRef.current = heal.attempt;
  }, [heal.attempt]);

  // When heal succeeds (server re-registered) the hook bumps recoveryVersion.
  // We can't just re-run the load effect on it directly — we need a fresh
  // token-protected URL first. Calling reload() on the URL hook does that.
  useEffect(() => {
    if (heal.recoveryVersion > 0) reload();
  }, [heal.recoveryVersion, reload]);

  useEffect(() => {
    if (!url) return;
    setError(null);
    let disposed = false;
    let chart: (ChartInstanceLike & { dispose: () => void; resize: () => void }) | null = null;
    let observer: ResizeObserver | null = null;

    (async () => {
      try {
        const echarts = await import('echarts');
        const text = await fetchArtifactText(url);
        const option = JSON.parse(text);
        if (disposed || !ref.current) return;

        const node = ref.current;
        const init = () => {
          if (disposed || chart) return;
          type EChart = ChartInstanceLike & {
            dispose: () => void;
            resize: () => void;
            setOption: (o: unknown) => void;
          };
          let created: EChart | null = null;
          try {
            // Pick the NM theme that matches the current dark/light state
            // (registered at app boot via main.tsx side effect).
            const c = echarts.init(node, pickNMTheme()) as unknown as EChart;
            created = c;
            c.setOption(option);
            chart = c;
            registerChartInstance(artifact.artifact_id, c);
          } catch (e) {
            // init runs from the ResizeObserver callback (a separate call
            // stack), so a throw here would NOT reach the outer catch — the
            // deferred-init path (0802 ①) would leave a permanently blank
            // pane with no error banner. Surface it and stop re-throwing on
            // every subsequent resize tick.
            // If echarts.init succeeded but setOption threw (a malformed
            // option is this component's documented, expected failure), the
            // canvas exists but `chart` is still null, so cleanup's
            // chart?.dispose() would leak it — dispose it here. Only when
            // chart is still null (a throw after registration means the
            // instance is owned; cleanup disposes it, no double-dispose).
            if (chart === null) {
              try {
                created?.dispose();
              } catch {
                /* a half-initialised instance may resist dispose; ignore */
              }
            }
            if (disposed) return;
            setError(String(e));
            observer?.disconnect();
          }
        };

        // One observer drives BOTH lifecycles (0802 bugs ①②): init is
        // deferred until the container actually has area — the LRU pool
        // mounts charts under display:none, and echarts.init on a 0×0 box
        // yields a permanently blank canvas — and every later box change
        // (column drag, window resize, zoom modal, collapse) re-fits via
        // chart.resize(), which this codebase previously never called.
        observer = new ResizeObserver(() => {
          if (disposed) return;
          const { width, height } = node.getBoundingClientRect();
          if (width > 0 && height > 0) {
            if (!chart) init();
            else chart.resize();
          }
        });
        observer.observe(node);
        // Visible-at-mount fast path: don't wait for the first RO tick.
        const { width, height } = node.getBoundingClientRect();
        if (width > 0 && height > 0) init();
      } catch (e) {
        const msg = String(e);
        setError(msg);
        // 410 → broken pointer (file_path NULL or off-disk). Kick off the
        // self-heal flow so the user gets candidates from their workspace
        // instead of a dead "Chart failed: 410" badge.
        if (msg.includes('fetch failed: 410')) {
          attemptRef.current();
        }
      }
    })();

    return () => {
      disposed = true;
      observer?.disconnect();
      // Identity-checked unregister: with the zoom modal and the column
      // both mounting this artifact, a naive null-write here erased the
      // OTHER mount's live registration (0802 bug ②).
      if (chart) unregisterChartInstance(artifact.artifact_id, chart);
      chart?.dispose();
    };
  }, [url, artifact.artifact_id, registerChartInstance, unregisterChartInstance]);

  return (
    <>
      {urlError ? (
        <div className="p-4 text-red-400">Chart failed: {urlError}</div>
      ) : error ? (
        <div className="p-4 text-red-400">Chart failed: {error}</div>
      ) : (
        <div ref={ref} className="w-full h-full" />
      )}
      <ArtifactHealModal
        open={heal.modalOpen}
        artifactTitle={artifact.title}
        candidates={heal.candidates}
        message={heal.message}
        busy={heal.busy}
        onPick={(workspacePath) => heal.attempt(workspacePath)}
        onDismiss={heal.dismiss}
      />
    </>
  );
}
