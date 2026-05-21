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
  const heal = useArtifactHeal(artifact.agent_id, artifact.artifact_id);

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
    let chart: { dispose: () => void } | null = null;

    (async () => {
      try {
        const echarts = await import('echarts');
        const text = await fetchArtifactText(url);
        const option = JSON.parse(text);
        if (disposed || !ref.current) return;
        // Pick the NM theme that matches the current dark/light state.
        // lib/echarts-nm-theme registers nm-light / nm-dark at app boot
        // (side-effect in main.tsx) so the names always resolve.
        const c = echarts.init(ref.current, pickNMTheme());
        c.setOption(option);
        chart = c as unknown as { dispose: () => void };
        registerChartInstance(artifact.artifact_id, c as unknown as ChartInstanceLike);
      } catch (e) {
        const msg = String(e);
        setError(msg);
        // 410 → broken pointer (file_path NULL or off-disk). Kick off the
        // self-heal flow so the user gets candidates from their workspace
        // instead of a dead "Chart failed: 410" badge.
        if (msg.includes('fetch failed: 410')) {
          heal.attempt();
        }
      }
    })();

    return () => {
      disposed = true;
      registerChartInstance(artifact.artifact_id, null);
      chart?.dispose();
    };
  }, [url, artifact.artifact_id, registerChartInstance, heal]);

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
