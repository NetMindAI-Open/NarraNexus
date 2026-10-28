/**
 * @file_name: ResumedRunChip.tsx
 * @date: 2026-08-21
 * @description: "Resumed the ongoing run" badge for the live streaming block.
 *
 * Shenzhen-r2 B1: a page refresh mid-run auto-reconnects and the backend
 * replays the WHOLE event stream from seq 0 — with no label, testers read
 * the replay as "it started generating again from scratch" and logged a
 * 10-minute run as a regeneration. This chip anchors the turn to the run's
 * REAL start time (run_reconnect frame's started_at, held in
 * chatStore.resumedRun) so the user sees one continuing run, not a new one.
 *
 * Presentation-only (iron rule #16): the replay itself is untouched — same
 * frames, same order, same content. Elapsed ticks every 30s while mounted;
 * a sub-minute or clock-skewed anchor clamps to 1 so the badge never says
 * "0 min" or a negative number.
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { RotateCw } from 'lucide-react';

const TICK_MS = 30_000;

function elapsedMinutes(startedAtMs: number): number {
  return Math.max(1, Math.floor((Date.now() - startedAtMs) / 60_000));
}

export default function ResumedRunChip({ startedAtMs }: { startedAtMs: number }) {
  const { t } = useTranslation();
  // Render-time derivation driven by a bare tick: minutes recomputes on
  // every render (covers a startedAtMs prop swap for free), the interval
  // only forces a re-render — no setState-in-effect.
  const [, setTick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setTick((n) => n + 1), TICK_MS);
    return () => clearInterval(timer);
  }, []);
  const minutes = elapsedMinutes(startedAtMs);

  return (
    <div className="mb-2 inline-flex items-center gap-1.5 px-2 py-1 text-[10px] uppercase tracking-[0.16em] font-mono text-[var(--text-tertiary)] border border-[var(--border-default)] rounded-[var(--radius-sm)] bg-[var(--bg-secondary)]">
      <RotateCw className="w-3 h-3" />
      <span>{t('chat.execution.resumedElapsed', { minutes })}</span>
    </div>
  );
}
