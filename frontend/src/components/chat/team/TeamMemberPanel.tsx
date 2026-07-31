/**
 * @file_name: TeamMemberPanel.tsx
 * @author:
 * @date: 2026-07-31
 * @description: One member's process, rendered as the same terminal
 *   card the single-agent ProcessPanel is — chrome header (live dot,
 *   `name · process`, ops + elapsed), phase rows, `$` tool rows, the
 *   `❯▌` cursor while alive.
 *
 * The live body is REAL: a running/stalled member is observed through
 * `useRunObservation(event_id)` — the platform's universal run
 * observation channel (WS replay + live continuation, cross-process via
 * the endpoint's DB tail-follow) — so the roster shows the same
 * thinking/tool stream the member's owner would see in single chat,
 * not a phase summary. The poll's phase timeline covers only the first
 * moments before the observation socket delivers.
 *
 * An idle member keeps the persisted `event_log` view (TurnTimeline —
 * the reasoning-&-tools renderer, owner decision 2026-07-31): history
 * questions are answered by history data, live questions by the live
 * stream.
 */

import { useEffect, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import { elapsedSince } from '@/lib/teamActivity';
import { useRunObservation } from '@/hooks/useRunObservation';
import {
  LiveCursorRow,
  LiveDot,
  PHASE_LABEL_KEYS,
  PhaseRow,
  ProcessEventRows,
} from '../process/processShared';
import { TurnTimeline } from '../TurnTimeline';
import { isProcessEvent, useTurnDetail } from './useTurnDetail';
import type { TurnEvent } from '@/types';
import type { TeamMemberActivity } from '@/types/teams';

/** Same bargain as ProcessPanel: follow the bottom unless the user
 *  scrolled up to read something. */
const FOLLOW_THRESHOLD_PX = 24;

/** Max card height — the roster column must keep multiple rows visible. */
const MAX_BODY_CLASS = 'max-h-[52vh]';

function PlanBlock({ plan }: { plan: Extract<TurnEvent, { type: 'plan' }> }) {
  const { t } = useTranslation();
  const done = plan.steps.filter((s) => s.status === 'completed').length;
  return (
    <div className="border-t border-[var(--border-subtle)] bg-[var(--nm-paper-warm)] px-3 py-2">
      <div className="mb-1 flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-[0.18em]" style={{ color: 'var(--nm-ink50)' }}>
          {t('chat.process.plan', 'Plan')}
        </span>
        <span className="text-[10px] tabular-nums" style={{ color: 'var(--nm-ink50)' }}>
          {done}/{plan.steps.length}
        </span>
      </div>
      <div className="space-y-0.5">
        {plan.steps.map((s, i) => {
          const active = s.status === 'in_progress';
          const finished = s.status === 'completed';
          return (
            <div
              key={`${i}-${s.step}`}
              className="flex items-center gap-2"
              style={{
                color: active
                  ? 'var(--color-silicon)'
                  : finished
                    ? 'var(--nm-ink50)'
                    : 'var(--nm-ink70)',
                fontWeight: active ? 600 : 400,
              }}
            >
              <span aria-hidden="true" className="shrink-0 select-none">
                {finished ? '✓' : active ? '▶' : '○'}
              </span>
              <span className={cn(finished && 'line-through')}>{s.step}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export interface TeamMemberPanelProps {
  activity: TeamMemberActivity;
  name: string;
  /** Shared 1s clock (epoch ms) — every duration on screen agrees. */
  now: number;
  open: boolean;
}

/** The expanded body of a roster row — a mini ProcessPanel. */
export function TeamMemberPanel({ activity, name, now, open }: TeamMemberPanelProps) {
  const { t } = useTranslation();
  const live = activity.status === 'running' || activity.status === 'stalled';

  // Live members are observed through the universal run channel;
  // idle members read the persisted event_log. Exactly one is active.
  const observation = useRunObservation(activity.event_id ?? null, {
    enabled: open && live && !!activity.event_id,
  });
  const detail = useTurnDetail(activity.agent_id, live ? null : activity.event_id, open);

  const scrollRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);

  const processEvents = useMemo(
    () => observation.events.filter(
      (e) => e.type === 'thinking' || e.type === 'tool_call' || e.type === 'tool_output',
    ),
    [observation.events],
  );
  const plan = useMemo(() => {
    for (let i = observation.events.length - 1; i >= 0; i -= 1) {
      const e = observation.events[i];
      if (e.type === 'plan') return e;
    }
    return undefined;
  }, [observation.events]);

  // Tool sub-steps are already tool rows — repeating them as phases
  // would double every call (same rule ProcessPanel applies).
  const phases = useMemo(
    () => observation.steps.filter((s) => !s.step.startsWith('3.4')),
    [observation.steps],
  );

  useEffect(() => {
    const el = scrollRef.current;
    if (el && followRef.current) el.scrollTop = el.scrollHeight;
  }, [processEvents, phases, plan]);

  if (!open) return null;

  // A result from the PREVIOUS turn must not be shown under the current
  // one: only a state whose key still matches counts as settled.
  const settled =
    detail && detail.key === `${activity.agent_id}:${activity.event_id}` ? detail : null;
  const idleEvents =
    !live && settled?.kind === 'ready' ? settled.events.filter(isProcessEvent) : null;
  const idleOps = idleEvents
    ? idleEvents.filter((e) => e.type === 'tool_call').length
    : 0;

  const stalled = activity.status === 'stalled';
  const dotColor = live
    ? (stalled ? 'var(--color-warning)' : 'var(--color-success)')
    : 'var(--nm-ink30)';

  let body: React.ReactNode;
  if (live && observation.errorMessage && observation.events.length === 0) {
    // The observe channel answered with a terminal error (e.g. the run
    // is not visible to this client) — say so instead of spinning a
    // "starting up" promise that can never be kept.
    body = <span className="text-[var(--text-tertiary)]">{t('chat.team.detailLoadFailed')}</span>;
  } else if (live) {
    const hasObservation = processEvents.length > 0 || phases.length > 0;
    body = (
      <>
        {phases.map((phase) => {
          const key = PHASE_LABEL_KEYS[phase.step];
          const settledPhase =
            phase.status === 'completed' ||
            phases.some((s) => parseFloat(s.step) > parseFloat(phase.step)) ||
            (processEvents.length > 0 && parseFloat(phase.step) < 3.4);
          return (
            <PhaseRow
              key={phase.step}
              done={settledPhase}
              label={key ? t(key) : phase.title}
            />
          );
        })}
        {!hasObservation && (
          <div className="flex items-center gap-2 py-0.5">
            <Loader2 className="h-3 w-3 shrink-0 animate-spin" style={{ color: 'var(--color-silicon)' }} />
            <span style={{ color: 'var(--nm-ink50)' }}>
              {t('chat.execution.startingUp', 'Starting up…')}
            </span>
          </div>
        )}
        <ProcessEventRows process={processEvents} />
        {observation.status !== 'ended' && <LiveCursorRow />}
      </>
    );
  } else if (!activity.event_id) {
    body = <span className="text-[var(--text-tertiary)]">{t('chat.team.noProcess')}</span>;
  } else if (!settled) {
    body = (
      <span className="flex items-center gap-2 text-[var(--text-tertiary)]">
        <Loader2 className="h-3 w-3 shrink-0 animate-spin" style={{ color: 'var(--color-silicon)' }} />
        {t('chat.team.roster.loading')}
      </span>
    );
  } else if (settled.kind === 'ready') {
    body = <TurnTimeline events={idleEvents ?? []} />;
  } else if (settled.kind === 'error') {
    // A failed fetch is not "no record" — say so; collapsing and
    // re-expanding retries (useTurnDetail clears its marker on failure).
    body = <span className="text-[var(--text-tertiary)]">{t('chat.team.detailLoadFailed')}</span>;
  } else {
    body = <span className="text-[var(--text-tertiary)]">{t('chat.team.noProcess')}</span>;
  }

  return (
    <div
      data-testid={`member-panel-${activity.agent_id}`}
      className="mx-2 mb-2 animate-fade-in overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--nm-paper)] shadow-sm"
      style={{ fontFamily: 'var(--font-mono)' }}
    >
      {/* Terminal chrome — the member's own header bar. */}
      <div className="flex items-center gap-2 border-b border-[var(--border-subtle)] bg-[var(--nm-paper-warm)] px-3 py-1.5">
        <LiveDot color={dotColor} live={live && !stalled} />
        <span
          className="min-w-0 truncate text-[10px] uppercase tracking-[0.18em]"
          style={{ color: 'var(--nm-ink70)' }}
        >
          {name} · {t('chat.team.roster.process', 'process')}
        </span>
        <span
          className="ml-auto flex shrink-0 items-center gap-3 text-[10px] tabular-nums"
          style={{ color: 'var(--nm-ink50)' }}
        >
          {live ? (
            <>
              {observation.opsCount > 0 && (
                <span>{observation.opsCount} {t('chat.process.ops', 'ops')}</span>
              )}
              <span>{elapsedSince(activity.started_at, now)}</span>
            </>
          ) : (
            idleOps > 0 && <span>{idleOps} {t('chat.process.ops', 'ops')}</span>
          )}
        </span>
      </div>

      <div
        ref={scrollRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          followRef.current =
            el.scrollHeight - el.scrollTop - el.clientHeight < FOLLOW_THRESHOLD_PX;
        }}
        className={cn(MAX_BODY_CLASS, 'overflow-y-auto px-3 py-2 text-xs leading-relaxed')}
      >
        {(activity.steps?.dropped ?? 0) > 0 && live && (
          <div className="pb-1 text-[10px] text-[var(--text-tertiary)]">
            {t('chat.team.activity.stepsDropped', { n: activity.steps?.dropped ?? 0 })}
          </div>
        )}
        {body}
        {stalled && (
          <div className="pt-1 text-[10px]" style={{ color: 'var(--color-warning)' }}>
            {t('chat.team.activity.silentFor', {
              duration: elapsedSince(activity.last_signal_at, now),
            })}
          </div>
        )}
      </div>

      {plan && live && <PlanBlock plan={plan} />}
    </div>
  );
}
