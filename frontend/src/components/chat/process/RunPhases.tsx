/**
 * @file_name: RunPhases.tsx
 * @date: 2026-08-31
 * @description: The run's preamble — backend pipeline phases at the head of
 *               the in-flight document.
 *
 * Why it exists: `segmentTurn` can only show what the model produced, and the
 * backend does real work before that — narrative selection, module load,
 * instance sync, context build. Between "send" and the first narration the
 * document would otherwise be blank, with nothing to say the run had started.
 *
 * Why it is not a panel: this replaces ProcessPanel's framed terminal box.
 * That box put a second visual register on the same screen as the frameless
 * agent turn — the one thing the document-flow pass set out to remove. The
 * information stayed; only the frame went. The mono glyph language (`»` rows,
 * ✓/spinner) is kept, because it reads as machine scaffolding rather than as
 * something the agent said, which is exactly what these rows are.
 *
 * Scope: strictly what the flow does NOT carry. The process events themselves
 * (narration, tool lines, reasoning) render through TurnTimeline in the flow;
 * drawing them here too would paint every row twice.
 */
import { memo, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import type { Step, TurnEvent } from '@/types';
import {
  PHASE_LABEL_KEYS,
  PHASE_STEP_IDS,
  phaseSettled,
  PhaseRow,
  formatElapsed,
} from './processShared';

export interface RunPhasesProps {
  events: TurnEvent[];
  /** Pipeline progress steps (chatStore.currentSteps). */
  steps?: Step[];
  /** When this run actually began, for a run picked up after a refresh or a
   *  reconnect. Mount time is a lie there — the run may be twenty minutes
   *  old — and this preamble now sits in the same column as ResumedRunChip,
   *  which reads the true elapsed from the same anchor. Two clocks side by
   *  side disagreeing is worse than either alone. */
  startedAtMs?: number;
}

/** Ticks once a second. Anchored to the run's real start when the caller
 *  knows it, else to mount — which for a fresh run is the same instant. */
function useElapsedSeconds(startedAtMs?: number): number {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const started = startedAtMs ?? Date.now();
    const tick = () => setElapsed(Math.max(0, Math.floor((Date.now() - started) / 1000)));
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [startedAtMs]);
  return elapsed;
}

export const RunPhases = memo(function RunPhases({
  events,
  steps = [],
  startedAtMs,
}: RunPhasesProps) {
  const { t } = useTranslation();
  const elapsed = useElapsedSeconds(startedAtMs);

  const process = useMemo(
    () => events.filter(
      (e) => e.type === 'thinking' || e.type === 'tool_call' || e.type === 'tool_output',
    ),
    [events],
  );

  const toolCount = useMemo(
    () => process.filter((e) => e.type === 'tool_call').length,
    [process],
  );

  // Only whitelisted top-level phases (PHASE_STEP_IDS). Tool sub-steps (3.4.x)
  // are already tool lines in the flow; the 3.5 final-thinking echo and the
  // post-answer housekeeping steps (4/5) aren't "what's happening now" — and
  // only whitelisted ids have localized labels, so this also stops raw English
  // backend titles from leaking into the UI.
  const phases = useMemo(
    () => steps.filter((s) => PHASE_STEP_IDS.has(s.step)),
    [steps],
  );

  // Settled-vs-running is the shared rule, fed the UNFILTERED steps so "a
  // later phase started" can see ids beyond the whitelist.
  const phaseDone = (phase: Step): boolean =>
    phaseSettled(phase, steps, process.length > 0);

  return (
    <div
      data-testid="run-phases"
      className="mb-2 text-xs leading-relaxed"
      style={{ fontFamily: 'var(--font-mono)' }}
    >
      {phases.map((phase) => {
        const key = PHASE_LABEL_KEYS[phase.step];
        return (
          <PhaseRow
            key={phase.step}
            done={phaseDone(phase)}
            label={key ? t(key) : phase.title}
          />
        );
      })}

      {phases.length === 0 && process.length === 0 && (
        <div className="flex items-center gap-2 py-0.5">
          <Loader2 className="h-3 w-3 shrink-0 animate-spin" style={{ color: 'var(--color-silicon)' }} />
          <span style={{ color: 'var(--nm-ink50)' }}>
            {t('chat.execution.startingUp', 'Starting up…')}
          </span>
        </div>
      )}

      {/* Ops count + elapsed. These were in the retired panel's chrome bar;
          losing them to the reframe would cost the user a datum (iron rule
          #16), so they carry over as a quiet trailing line. */}
      <div
        data-testid="run-phases-meta"
        className="flex items-center gap-3 py-0.5 text-[10px] tabular-nums"
        style={{ color: 'var(--nm-ink50)' }}
      >
        <span>{toolCount} {t('chat.process.ops', 'ops')}</span>
        <span>{formatElapsed(elapsed)}</span>
      </div>
    </div>
  );
});
