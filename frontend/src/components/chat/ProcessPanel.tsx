/**
 * ProcessPanel — while the agent works, the process lives here; the
 * answer lives in the bubbles above.
 *
 * Why a separate panel: process and replies used to share one
 * chronological TurnTimeline, tiered only by solid-vs-dashed borders.
 * Nothing was missing, but the reader had to find the answer inside the
 * noise. Split out, the bubble carries only the answer and the process
 * scrolls here continuously — scannable like a terminal, not something
 * to read.
 *
 * v3 (2026-07-30, owner feedback round 2):
 * - The pipeline phases ("loading context", "building context", …) that
 *   used to float as a lone spinner in the message area now render as
 *   `»` rows at the top of the panel — one surface for everything the
 *   agent is doing, from init to the last tool. The panel therefore
 *   renders from the moment streaming starts (a bare "Starting up…"
 *   header), never null while mounted.
 * - Collapsible. Collapsed = one or two lines: the current activity
 *   (latest tool / thinking / phase) + elapsed time, plus a plan
 *   progress line only when a plan exists.
 * - Color per species: `»` phases and tool names in silicon, `$` in
 *   success green, `∴` thinking glyph in carbon, pending in warning
 *   amber, outputs receded — all theme tokens, so light and dark hold.
 *
 * The plan is pinned below the scroll area: it answers "where are we
 * now" and must not scroll away. Plans are full snapshots
 * (replace-on-write), so only the latest one renders.
 *
 * Mounted only while streaming; ChatPanel unmounts it when the turn
 * ends and the process folds back into each reply's bubble (see
 * lib/segmentTurn). So nothing is persisted here — this is a viewport,
 * not storage.
 */
import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronUp, Loader2 } from 'lucide-react';
import type { Step, TurnEvent } from '@/types';
import { cn } from '@/lib/utils';
import {
  PHASE_LABEL_KEYS,
  LiveCursorRow,
  LiveDot,
  PhaseRow,
  formatElapsed,
  deriveActivity,
  ProcessEventRows,
} from './process/processShared';
import type { Activity } from './process/processShared';

export interface ProcessPanelProps {
  events: TurnEvent[];
  /** Pipeline progress steps (chatStore.currentSteps) — the pre-loop
   *  phases render as rows so "loading context" lives here, not as a
   *  floating spinner in the message area. */
  steps?: Step[];
}

/** Max panel height as a viewport fraction — any taller pushes the composer out of view. */
const MAX_HEIGHT_CLASS = 'max-h-[40vh]';

/** Within how many pixels of the bottom still counts as "following".
 *  One wheel notch is ~100px, so 24 separates "the user scrolled up"
 *  from browser scroll rounding error. */
const FOLLOW_THRESHOLD_PX = 24;

/** Ticks once a second from mount; the panel mounts when the turn
 *  starts, so this reads as the turn's elapsed time. */
function useElapsedSeconds(): number {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const timer = setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => clearInterval(timer);
  }, []);
  return elapsed;
}

export const ProcessPanel = memo(function ProcessPanel({ events, steps = [] }: ProcessPanelProps) {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);
  const elapsed = useElapsedSeconds();
  const [collapsed, setCollapsed] = useState(false);

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

  // Pre-loop pipeline phases. Tool sub-steps (3.4.x) are already the
  // panel's tool rows — repeating them as phases would double every call.
  const phases = useMemo(
    () => steps.filter((s) => !s.step.startsWith('3.4')),
    [steps],
  );

  // A phase is settled once its own status says so or a later phase has
  // started (the backend keeps early phases "running" until the turn
  // ends, so ordering is the honest signal).
  const phaseDone = (phase: Step): boolean => {
    if (phase.status === 'completed') return true;
    const n = parseFloat(phase.step);
    return steps.some((s) => parseFloat(s.step) > n) ||
      (process.length > 0 && n < 3.4);
  };

  // Plans are full snapshots: each update replaces the previous one
  // wholesale, so take the last.
  const plan = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i -= 1) {
      const e = events[i];
      if (e.type === 'plan') return e;
    }
    return undefined;
  }, [events]);

  const planDone = plan ? plan.steps.filter((s) => s.status === 'completed').length : 0;
  const planActive = plan?.steps.find((s) => s.status === 'in_progress');

  const activity: Activity = deriveActivity(process, phases, t);

  // Auto-scroll to the bottom unless the user scrolled up — the same
  // bargain the message area makes: following is the default, but once
  // someone says "I want to look up there", never steal the viewport.
  useEffect(() => {
    const el = scrollRef.current;
    if (el && followRef.current) el.scrollTop = el.scrollHeight;
  }, [process, plan, phases]);

  return (
    <div
      data-testid="process-panel"
      className="mb-2 overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--nm-paper)] shadow-sm"
      style={{ fontFamily: 'var(--font-mono)' }}
    >
      {/* Terminal chrome — the whole bar toggles collapse. Live dot on
          the left; collapsed shows the current activity inline; timer
          (and op counter when expanded) on the right. */}
      <button
        type="button"
        data-testid="process-panel-header"
        onClick={() => setCollapsed((c) => !c)}
        className="flex w-full items-center gap-2 border-b border-[var(--border-subtle)] bg-[var(--nm-paper-warm)] px-3 py-1.5 text-left"
      >
        <LiveDot color="var(--color-success)" live />
        {collapsed ? (
          <span
            data-testid="process-activity"
            className="flex min-w-0 items-center gap-1.5 text-xs"
          >
            {activity.pending && (
              <Loader2 className="h-3 w-3 shrink-0 animate-spin" style={{ color: 'var(--color-warning)' }} />
            )}
            {activity.tool && !activity.pending && (
              <span aria-hidden="true" className="shrink-0 font-semibold" style={{ color: 'var(--color-success)' }}>$</span>
            )}
            <span
              className="truncate font-semibold"
              style={{ color: activity.tool ? 'var(--color-silicon)' : 'var(--nm-ink70)' }}
            >
              {activity.text}
            </span>
          </span>
        ) : (
          <span
            className="text-[10px] uppercase tracking-[0.18em]"
            style={{ color: 'var(--nm-ink70)' }}
          >
            {t('chat.process.title', 'agent · process')}
          </span>
        )}
        <span
          className="ml-auto flex shrink-0 items-center gap-3 text-[10px] tabular-nums"
          style={{ color: 'var(--nm-ink50)' }}
        >
          {!collapsed && <span>{toolCount} {t('chat.process.ops', 'ops')}</span>}
          <span>{formatElapsed(elapsed)}</span>
          {collapsed
            ? <ChevronDown className="h-3 w-3" />
            : <ChevronUp className="h-3 w-3" />}
        </span>
      </button>

      {/* Collapsed second line: plan progress — only when a plan exists. */}
      {collapsed && plan && (
        <div
          data-testid="process-plan-mini"
          className="flex items-center gap-2 px-3 py-1 text-xs"
        >
          <span className="shrink-0 text-[10px] tabular-nums" style={{ color: 'var(--nm-ink50)' }}>
            {planDone}/{plan.steps.length}
          </span>
          <span
            className="h-1 w-16 shrink-0 overflow-hidden rounded-full"
            style={{ background: 'var(--nm-ink30)' }}
          >
            <span
              className="block h-full rounded-full transition-[width] duration-500"
              style={{
                width: `${plan.steps.length ? Math.round((planDone / plan.steps.length) * 100) : 0}%`,
                background: 'var(--color-success)',
              }}
            />
          </span>
          {planActive && (
            <span className="flex min-w-0 items-center gap-1.5" style={{ color: 'var(--color-silicon)' }}>
              <span aria-hidden="true" className="shrink-0">▶</span>
              <span className="truncate">{planActive.step}</span>
            </span>
          )}
        </div>
      )}

      {!collapsed && (
        <>
          <div
            ref={scrollRef}
            onScroll={(e) => {
              const el = e.currentTarget;
              followRef.current =
                el.scrollHeight - el.scrollTop - el.clientHeight < FOLLOW_THRESHOLD_PX;
            }}
            className={cn(MAX_HEIGHT_CLASS, 'overflow-y-auto px-3 py-2 text-xs leading-relaxed')}
          >
            {/* Pipeline phase rows — the "loading context…" lines that
                used to float in the message area live here now. */}
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

            <ProcessEventRows process={process} />
            {/* Live cursor — the terminal's "still running" heartbeat. */}
            <LiveCursorRow />
          </div>

          {plan && (
            <div
              data-testid="process-plan"
              className="border-t border-[var(--border-subtle)] bg-[var(--nm-paper-warm)] px-3 py-2 text-xs"
            >
              <div className="mb-1.5 flex items-center gap-2">
                <span
                  className="text-[10px] uppercase tracking-[0.18em]"
                  style={{ color: 'var(--nm-ink50)' }}
                >
                  {t('chat.process.plan', 'Plan')}
                </span>
                <span className="text-[10px] tabular-nums" style={{ color: 'var(--nm-ink50)' }}>
                  {planDone}/{plan.steps.length}
                </span>
                {/* Mini progress bar: width follows completed/total. */}
                <span
                  className="ml-1 h-1 flex-1 overflow-hidden rounded-full"
                  style={{ background: 'var(--nm-ink30)', maxWidth: 96 }}
                >
                  <span
                    className="block h-full rounded-full transition-[width] duration-500"
                    style={{
                      width: `${plan.steps.length ? Math.round((planDone / plan.steps.length) * 100) : 0}%`,
                      background: 'var(--color-success)',
                    }}
                  />
                </span>
              </div>
              <div className="space-y-0.5">
                {plan.steps.map((s, i) => {
                  const active = s.status === 'in_progress';
                  const done = s.status === 'completed';
                  return (
                    <div
                      key={`${i}-${s.step}`}
                      className="flex items-center gap-2"
                      style={{
                        color: active
                          ? 'var(--color-silicon)'
                          : done
                            ? 'var(--nm-ink50)'
                            : 'var(--nm-ink70)',
                        fontWeight: active ? 600 : 400,
                      }}
                    >
                      <span aria-hidden="true" className="shrink-0 select-none">
                        {done ? '✓' : active ? '▶' : '○'}
                      </span>
                      <span className={cn(done && 'line-through')}>{s.step}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
});
