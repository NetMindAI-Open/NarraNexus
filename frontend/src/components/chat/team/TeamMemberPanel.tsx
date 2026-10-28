/**
 * @file_name: TeamMemberPanel.tsx
 * @author:
 * @date: 2026-07-31
 * @description: One member's process, rendered as a terminal card —
 *   chrome header (live dot, `name · process`, ops + elapsed), phase
 *   rows, the observed turn's own rows, the `❯▌` cursor while alive.
 *
 *   It KEEPS its card chrome, unlike single chat, whose framed process
 *   panel was dissolved on 2026-08-31. The two are not the same surface:
 *   this is one row of a roster listing several members, so the frame is
 *   what separates member from member. Single chat has one agent and one
 *   reading column, where a second frame only competed with the turn.
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

import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { dispatchAgentCircuitOpen } from '@/services/wsCircuitOpen';
import { Loader2, Square } from 'lucide-react';
import { cn } from '@/lib/utils';
import { api } from '@/lib/api';
import { elapsedSince } from '@/lib/teamActivity';
import { useRunObservation } from '@/hooks/useRunObservation';
import {
  LiveCursorRow,
  LiveDot,
  PHASE_LABEL_KEYS,
  PHASE_STEP_IDS,
  PhaseRow,
  phaseSettled,
} from '../process/processShared';
import { TurnTimeline } from '../TurnTimeline';
import { isProcessEvent, useTurnDetail } from './useTurnDetail';
import type { TurnEvent } from '@/types';
import type { TeamMemberActivity } from '@/types/teams';

/** Same bargain the message area makes: follow the bottom unless the
 *  user scrolled up to read something. */
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

/** The expanded body of a roster row — one member's terminal card. */
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

  // The stop request is fire-and-observe: the endpoint records intent and
  // returns, the run is interrupted in another process, and the terminal
  // state arrives on the observation stream. This state only covers the gap
  // between those two — the answer the 8-minute black box never gave.
  const [stopRequest, setStopRequest] = useState<
    { runId: string; failed: boolean } | null
  >(null);
  const runId = activity.event_id ?? null;
  // A stop belongs to the run it was aimed at; a new turn starts clean.
  const stopping = !!stopRequest && stopRequest.runId === runId && !stopRequest.failed;
  const stopFailed = !!stopRequest && stopRequest.runId === runId && stopRequest.failed;

  const requestStop = async () => {
    if (!runId || stopping) return;
    setStopRequest({ runId, failed: false });
    try {
      const r = await api.cancelRun(runId);
      if (r.already_settled) {
        // The run finished between render and click. Nothing was flagged, so
        // no terminal frame is coming — holding "stopping…" would wait forever
        // for an event that already happened.
        setStopRequest(null);
      }
    } catch {
      // Owner check rejected, run already gone, network down — the button
      // must not stay stuck on "stopping" for something that never landed.
      setStopRequest({ runId, failed: true });
    }
  };

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

  // Only the whitelisted top-level phases (same rule RunPhases applies):
  // tool sub-steps are already tool rows, and housekeeping/echo steps aren't
  // phases — this also keeps raw English backend titles out of the panel.
  const phases = useMemo(
    () => observation.steps.filter((s) => PHASE_STEP_IDS.has(s.step)),
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

  // A paused agent is not a failed fetch. The room used to show
  // "couldn't load the process" for both, which is untrue for one of them and
  // leaves the user nothing to do either way.
  //
  // The honest surface already exists — App.tsx listens for this event and
  // renders a banner with a one-click Resume that clears the pause — and the
  // private chat has fired it for a long time. This panel announces because it
  // is the piece that knows WHICH agent: the observation hook only has a run id.
  // Reusing the event rather than building a second banner keeps the breaker's
  // reason vocabulary in one place.
  useEffect(() => {
    const reason = observation.circuitReason;
    if (!reason) return;
    dispatchAgentCircuitOpen({ agentId: activity.agent_id, reason });
    // The dependency array IS the "announce once" mechanism: the roster ticks a
    // 1s clock, and a render that changes neither the reason nor the agent does
    // not re-run this. An extra ref guard was tried and removed — it was dead
    // code against that, and its only real effect would have been to suppress a
    // legitimate re-announce after a breaker cleared and reopened.
  }, [observation.circuitReason, activity.agent_id]);

  let body: React.ReactNode;
  if (observation.circuitReason) {
    // Says what happened, and the app-wide banner carries the Resume.
    body = (
      <span data-testid="member-paused" className="text-[var(--text-tertiary)]">
        {t('chat.team.agentPaused', { reason: observation.circuitReason })}
      </span>
    );
  } else if (live && observation.errorMessage && observation.events.length === 0) {
    // The observe channel answered with a terminal error (e.g. the run
    // is not visible to this client) — say so instead of spinning a
    // "starting up" promise that can never be kept. Still the honest copy for
    // the honest case; widening it to cover a pause swapped one lie for another.
    body = <span className="text-[var(--text-tertiary)]">{t('chat.team.detailLoadFailed')}</span>;
  } else if (live) {
    const hasObservation = processEvents.length > 0 || phases.length > 0;
    body = (
      <>
        {phases.map((phase) => {
          const key = PHASE_LABEL_KEYS[phase.step];
          // Shared settled rule, fed the UNFILTERED observation.steps (not the
          // whitelisted `phases`) so "a later phase started" can see run-agent
          // / housekeeping ids the whitelist drops — same as RunPhases.
          return (
            <PhaseRow
              key={phase.step}
              done={phaseSettled(phase, observation.steps, processEvents.length > 0)}
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
        {/* Same renderer as the main chat's live turn and its settled one:
            two shapes for the same events is what this pass exists to end. */}
        {/* Same predicate as the live cursor below: an ENDED observation is
            not streaming, and saying it is keeps the blocks on the streaming
            plain-text path, so a finished member turn would not render the
            markdown the settled main chat renders for the same events. */}
        <TurnTimeline
          events={processEvents}
          isStreaming={observation.status !== 'ended'}
        />
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
      className="mx-2 mb-2 animate-fade-in overflow-hidden rounded-[var(--radius-lg)] border border-[var(--border-subtle)] bg-[var(--nm-paper)] shadow-sm"
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
              {runId && (
                <button
                  type="button"
                  onClick={requestStop}
                  disabled={stopping}
                  data-testid={`member-stop-${activity.agent_id}`}
                  title={t('chat.team.roster.stopHint')}
                  className={cn(
                    'flex items-center gap-1 rounded px-1.5 py-0.5 transition-colors',
                    stopping
                      ? 'cursor-default text-[var(--nm-ink50)]'
                      : 'text-[var(--nm-ink70)] hover:bg-[var(--color-warning)]/10 hover:text-[var(--color-warning)]',
                  )}
                >
                  {stopping ? (
                    <Loader2 className="h-3 w-3 shrink-0 animate-spin" />
                  ) : (
                    <Square className="h-3 w-3 shrink-0" />
                  )}
                  <span>
                    {stopping
                      ? t('chat.team.roster.stopping')
                      : t('chat.team.roster.stop')}
                  </span>
                </button>
              )}
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
        {stopFailed && (
          <div className="pt-1 text-[10px]" style={{ color: 'var(--color-warning)' }}>
            {t('chat.team.roster.stopFailed')}
          </div>
        )}
        {/* The terminal word matters: a stopped run rendered as "completed"
            tells the owner the task they killed ran to the end. */}
        {observation.endState === 'cancelled' && (
          <div className="pt-1 text-[10px]" style={{ color: 'var(--color-warning)' }}>
            {t('chat.team.roster.stopped')}
          </div>
        )}
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
