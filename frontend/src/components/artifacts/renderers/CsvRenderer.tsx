/**
 * @file_name: CsvRenderer.tsx
 * @description: Lazy-loaded renderer for text/csv artifacts.
 *
 * Fetches the raw CSV text and renders it as a scrollable HTML table.
 * Uses a naive comma-split parser — good enough for agent-generated tabular
 * output. Does NOT handle quoted fields containing commas (e.g. "a,b",c).
 * Swap parseCsv() for papaparse or csv-parse if proper RFC 4180 parsing is
 * needed later, without touching the rest of this component.
 *
 * Pointer model: content is fetched from the token-protected directory URL.
 */

import { useEffect, useState } from 'react';
import type { Artifact } from '@/types/artifact';
import { fetchArtifactText } from '@/services/artifactsApi';
import { useArtifactRawUrl } from '@/hooks/useArtifactRawUrl';

interface Props {
  artifact: Artifact;
}

function parseCsv(text: string): string[][] {
  return text
    .split(/\r?\n/)
    .filter((line) => line.length > 0)
    .map((line) => line.split(','));
}

export default function CsvRenderer({ artifact }: Props) {
  const { url, error: urlError } = useArtifactRawUrl(
    artifact.agent_id,
    artifact.artifact_id,
    artifact.updated_at,
  );
  const [rows, setRows] = useState<string[][] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!url) return;
    let cancelled = false;
    (async () => {
      setError(null);
      try {
        const text = await fetchArtifactText(url);
        if (!cancelled) setRows(parseCsv(text));
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    })();
    return () => { cancelled = true; };
  }, [url]);

  if (urlError) return <div className="p-4 text-red-400">Failed to load: {urlError}</div>;
  if (error) return <div className="p-4 text-red-400">Failed to load: {error}</div>;
  if (!rows) return <div className="p-4 opacity-60">Loading…</div>;
  if (rows.length === 0) return <div className="p-4 opacity-60">Empty CSV</div>;

  const [header, ...body] = rows;
  return (
    <div className="overflow-auto p-4">
      <table className="border-collapse text-sm">
        <thead>
          <tr>
            {header.map((cell, i) => (
              <th key={i} className="border border-[var(--border-default)] px-2 py-1 text-left bg-[var(--bg-primary)]">
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j} className="border border-[var(--border-default)] px-2 py-1">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
