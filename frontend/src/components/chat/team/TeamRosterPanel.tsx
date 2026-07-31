/**
 * @file_name: TeamRosterPanel.tsx
 * @author:
 * @date: 2026-07-30
 * @description: The team room's standing member column — every member,
 *   its status, its timer, and one click to that member's live process.
 *
 * A folded console answers "is anything happening" only after you ask it.
 * In a room where six agents work in parallel, the question the user
 * actually holds is "what is EACH of them doing, right now" — so the
 * roster is permanent chrome down the right edge: one row per member,
 * always, including members the activity poll never mentioned (absent
 * from `activity` = idle with no trace).
 *
 * v2 (2026-07-31, owner feedback: the column must not read cheap):
 * - The column BREATHES: 256px at rest, and when a member's detail is
 *   open it animates to 430px so the terminal card gets real width —
 *   the transcript pane (flex-1 min-w-0) yields automatically.
 * - Rows carry identity: RingAvatar + status-corner dot
 *   (AvatarWithStatus tones: running=green, queued=amber,
 *   stalled=error, idle=muted), a readable status word instead of a
 *   1.5px color dot, a chevron affordance, a selected state (accent
 *   rail + wash) that mirrors the transcript's typing-bubble highlight.
 * - The expanded detail is a mini ProcessPanel (TeamMemberPanel):
 *   running members stream REAL thinking/tool rows through the
 *   universal run-observation channel; idle members keep the persisted
 *   TurnTimeline. Same terminal language as single chat.
 *
 * Expansion is CONTROLLED (`expandedId` + `onToggle`): the parent needs
 * the same id to drive the transcript's typing bubble, and two
 * components owning one selection is how they drift apart.
 */

import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, ChevronDown } from 'lucide-react';
import { cn } from '@/lib/utils';
import { AvatarWithStatus, RingAvatar } from '@/components/nm';
import {
  STATUS_TONES,
  compareActivity,
  elapsedSince,
  formatDuration,
  lastRunSummary,
  phaseLabelKey,
} from '@/lib/teamActivity';
import { LiveDot, friendlyToolName } from '../process/processShared';
import { TeamMemberPanel } from './TeamMemberPanel';
import type { AgentInfo } from '@/types';
import type { TeamMemberActivity, TeamMemberStatus } from '@/types/teams';

export interface TeamRosterPanelProps {
  /** Every member of the room; only `agent_id` / `name` are consumed. */
  members: AgentInfo[];
  /** Polled activity. A member missing here is idle with no trace. */
  activity: TeamMemberActivity[];
  leadAgentId: string | null;
  /** One clock for the whole panel (1s tick, epoch ms) — driven by the parent
   *  so no two durations on screen disagree by a tick. */
  now: number;
  /** The member whose detail is open; null = all collapsed. */
  expandedId: string | null;
  /** Row click. The parent owns same-id → null (collapse). */
  onToggle: (agentId: string) => void;
  /** Team accent — the selected row wears it, matching the transcript's
   *  typing-bubble highlight. */
  accent?: string;
  /** Empty-state escape hatch to the team's settings page. */
  onOpenSettings?: () => void;
  /** Shell override — the narrow-screen drawer reuses the same rows. */
  className?: string;
}

/** Status → the avatar's corner-dot tone. Green = alive and working,
 *  amber = waiting, error = wedged, muted = nothing running. */
const AVATAR_STATUS: Record<TeamMemberStatus, 'success' | 'warning' | 'error' | 'neutral'> = {
  running: 'success',
  queued: 'warning',
  stalled: 'error',
  idle: 'neutral',
};

/** The row's right-hand metric: elapsed, wait, silence, or the last run. */
function RowMetric({ activity, now }: { activity: TeamMemberActivity; now: number }) {
  const { t } = useTranslation();

  if (activity.status === 'running') return <>{elapsedSince(activity.started_at, now)}</>;
  if (activity.status === 'stalled') {
    return (
      <>{t('chat.team.activity.silentFor', { duration: elapsedSince(activity.last_signal_at, now) })}</>
    );
  }
  if (activity.status === 'queued') {
    return (
      <>
        {t('chat.team.activity.waitingFor', { duration: elapsedSince(activity.queued_since, now) })}
        {(activity.queued_count ?? 0) > 1 && ` ×${activity.queued_count}`}
      </>
    );
  }

  const last = lastRunSummary(activity, now);
  if (!last) return <>{t('chat.team.roster.neverRan')}</>;
  // Legacy rows without started_at have no honest duration — say when it
  // finished rather than invent a "ran 0s".
  if (last.durationMs === null) {
    return <>{t('chat.team.roster.lastRunAgoOnly', { ago: formatDuration(last.agoMs) })}</>;
  }
  return (
    <>
      {t('chat.team.roster.lastRun', {
        duration: formatDuration(last.durationMs),
        ago: formatDuration(last.agoMs),
      })}
    </>
  );
}

/** The row's status line: `$ tool` while running, a readable status
 *  word otherwise — never a bare color dot the user has to decode. */
function StatusLine({ activity }: { activity: TeamMemberActivity }) {
  const { t } = useTranslation();
  const tone = STATUS_TONES[activity.status];

  if (activity.status === 'running' && activity.phase?.startsWith('tool:')) {
    return (
      <span className="flex min-w-0 items-center gap-1 font-mono text-[11px]">
        <span aria-hidden="true" className="shrink-0 select-none font-semibold" style={{ color: 'var(--color-success)' }}>
          $
        </span>
        <span className="truncate" style={{ color: 'var(--color-silicon)' }}>
          {friendlyToolName(activity.phase.slice(5))}
        </span>
      </span>
    );
  }
  if (activity.status === 'running') {
    const { key, values } = phaseLabelKey(activity.phase);
    return (
      <span className="min-w-0 truncate text-[11px]" style={{ color: 'var(--color-silicon)' }}>
        {t(key, values)}
      </span>
    );
  }
  if (activity.status === 'stalled') {
    return (
      <span className="flex min-w-0 items-center gap-1 text-[11px]" style={{ color: tone.color }}>
        <AlertTriangle className="h-3 w-3 shrink-0" />
        <span className="truncate">{t(tone.labelKey)}</span>
      </span>
    );
  }
  return (
    <span
      className="min-w-0 truncate text-[11px]"
      style={{ color: activity.status === 'queued' ? tone.color : 'var(--text-tertiary)' }}
    >
      {t(tone.labelKey)}
    </span>
  );
}

/** One member: avatar + name + status + metric, and its terminal card
 *  underneath when selected. */
function MemberRow({
  activity,
  name,
  isLead,
  accent,
  now,
  expanded,
  onToggle,
}: {
  activity: TeamMemberActivity;
  name: string;
  isLead: boolean;
  accent: string;
  now: number;
  expanded: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  const stalled = activity.status === 'stalled';

  return (
    <div
      className={cn(
        'border-b border-[var(--rule)] transition-colors',
        // A wedged worker gets a tinted row: the one state the user
        // should not have to go looking for.
        stalled && !expanded && 'bg-[var(--color-warning)]/5',
      )}
      style={
        expanded
          ? {
              // The selected member wears the team accent — the same
              // language as the transcript's highlighted typing bubble.
              boxShadow: `inset 2px 0 0 0 ${accent}`,
              background: 'color-mix(in srgb, var(--color-silicon) 4%, transparent)',
            }
          : undefined
      }
    >
      <button
        type="button"
        data-testid={`roster-row-${activity.agent_id}`}
        onClick={onToggle}
        aria-expanded={expanded}
        className="group flex w-full items-center gap-2.5 px-3 py-2.5 text-left transition-colors hover:bg-[var(--nm-paper-warm)]"
      >
        <AvatarWithStatus status={AVATAR_STATUS[activity.status]} className="shrink-0">
          <span className="relative inline-block">
            <RingAvatar species="silicon" label={name.slice(0, 2)} size="sm" />
            {isLead && (
              <span
                title={t('chat.team.leadTitle', { name })}
                className="absolute -left-0.5 -top-0.5 h-2 w-2 rounded-full border border-[var(--nm-paper)]"
                style={{ background: accent }}
              />
            )}
          </span>
        </AvatarWithStatus>

        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-medium text-[var(--nm-ink)]">
            {name}
          </span>
          <span className="mt-0.5 flex items-center gap-1.5">
            <StatusLine activity={activity} />
            <span className="ml-auto shrink-0 font-mono tabular-nums text-[10px] text-[var(--text-tertiary)]">
              <RowMetric activity={activity} now={now} />
            </span>
          </span>
        </span>

        <ChevronDown
          className={cn(
            'h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)] transition-transform duration-200',
            'opacity-0 group-hover:opacity-100',
            expanded && 'rotate-180 opacity-100',
          )}
        />
      </button>

      {expanded && (
        <TeamMemberPanel activity={activity} name={name} now={now} open={expanded} />
      )}
    </div>
  );
}

export function TeamRosterPanel({
  members,
  activity,
  leadAgentId,
  now,
  expandedId,
  onToggle,
  accent = 'var(--color-silicon)',
  onOpenSettings,
  className,
}: TeamRosterPanelProps) {
  const { t } = useTranslation();

  const nameOf = useMemo(() => {
    const names = new Map(members.map((m) => [m.agent_id, m.name || m.agent_id]));
    return (agentId: string) => names.get(agentId) ?? agentId;
  }, [members]);

  // Members drive the list, activity only decorates it: a member the poll has
  // never seen is idle with no trace, not an absent teammate.
  const rows = useMemo(() => {
    const byId = new Map(activity.map((a) => [a.agent_id, a]));
    return members
      .map<TeamMemberActivity>(
        (m) => byId.get(m.agent_id) ?? { agent_id: m.agent_id, status: 'idle' },
      )
      .sort((a, b) => compareActivity(a, b, nameOf));
  }, [members, activity, nameOf]);

  const workingCount = rows.filter((a) => a.status === 'running').length;

  return (
    <aside
      className={cn(
        'flex shrink-0 flex-col border-l border-[var(--rule)] min-h-0',
        // The column breathes: opening a member's terminal needs width,
        // and the transcript (flex-1 min-w-0) yields on its own.
        'transition-[width] duration-300 ease-[cubic-bezier(0.4,0,0.2,1)] motion-reduce:transition-none',
        expandedId ? 'w-[min(430px,92vw)]' : 'w-64',
        className,
      )}
    >
      <div className="flex shrink-0 items-center gap-1.5 border-b border-[var(--rule)] px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-[var(--text-tertiary)]">
        <span>{t('chat.team.roster.title')}</span>
        <span className="tabular-nums">{members.length}</span>
        {workingCount > 0 && (
          <span className="ml-auto flex items-center gap-1.5" style={{ color: 'var(--color-success)' }}>
            <LiveDot color="var(--color-success)" live />
            <span className="tabular-nums normal-case tracking-normal">
              {t('chat.team.roster.workingCount', { count: workingCount })}
            </span>
          </span>
        )}
      </div>

      {members.length === 0 ? (
        <div className="flex flex-1 flex-col items-start gap-2 px-3 py-3">
          <p className="text-xs leading-relaxed text-[var(--text-tertiary)]">
            {t('chat.team.noAgents')}
          </p>
          {onOpenSettings && (
            <button
              type="button"
              onClick={onOpenSettings}
              className="rounded-[var(--radius-xs)] border border-[var(--rule)] px-2 py-1 text-[11px] text-[var(--text-secondary)] transition-colors hover:bg-[var(--nm-paper-warm)] hover:text-[var(--color-carbon)]"
            >
              {t('chat.team.teamSettings')}
            </button>
          )}
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          {rows.map((a) => (
            <MemberRow
              key={a.agent_id}
              activity={a}
              name={nameOf(a.agent_id)}
              isLead={a.agent_id === leadAgentId}
              accent={accent}
              now={now}
              expanded={expandedId === a.agent_id}
              onToggle={() => onToggle(a.agent_id)}
            />
          ))}
        </div>
      )}
    </aside>
  );
}
