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

interface Props {
  artifact: Artifact;
}

export default function ChartRenderer({ artifact }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { url, error: urlError } = useArtifactRawUrl(
    artifact.agent_id,
    artifact.artifact_id,
    artifact.updated_at,
  );
  const registerChartInstance = useArtifactStore((s) => s.registerChartInstance);

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
        const c = echarts.init(ref.current);
        c.setOption(option);
        chart = c as unknown as { dispose: () => void };
        registerChartInstance(artifact.artifact_id, c as unknown as ChartInstanceLike);
      } catch (e) {
        setError(String(e));
      }
    })();

    return () => {
      disposed = true;
      registerChartInstance(artifact.artifact_id, null);
      chart?.dispose();
    };
  }, [url, artifact.artifact_id, registerChartInstance]);

  if (urlError) return <div className="p-4 text-red-400">Chart failed: {urlError}</div>;
  if (error) return <div className="p-4 text-red-400">Chart failed: {error}</div>;
  return <div ref={ref} className="w-full h-full" />;
}
