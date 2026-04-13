/**
 * @file_name: JobsSection.tsx
 * @description: v2.1 — collapsible "Jobs" section listing all live-state jobs
 * with state-specific visuals. Item-level expand loads full job detail.
 */
import { useState } from 'react';
import type {
  DashboardPendingJob,
  DashboardRunningJob,
  JobQueueStatus,
} from '@/types';
import { api } from '@/lib/api';
import { useExpanded } from './expandState';

interface Props {
  agentId: string;
  runningJobs: DashboardRunningJob[];
  pendingJobs: DashboardPendingJob[];
}

const STATE_META: Record<
  JobQueueStatus | 'running',
  { icon: string; label: string; cls: string }
> = {
  running:  { icon: '⚙️', label: 'running', cls: 'text-emerald-600' },
  active:   { icon: '🔵', label: 'active',  cls: 'text-sky-600' },
  pending:  { icon: '⚪️', label: 'pending', cls: 'text-gray-500' },
  blocked:  { icon: '🟠', label: 'blocked', cls: 'text-amber-600' },
  paused:   { icon: '🟡', label: 'paused',  cls: 'text-yellow-600' },
  failed:   { icon: '🔴', label: 'failed',  cls: 'text-red-600' },
};

export function JobsSection({ agentId, runningJobs, pendingJobs }: Props) {
  const { expanded, toggle } = useExpanded(`${agentId}:section:jobs`, false);
  const total = runningJobs.length + pendingJobs.length;
  if (total === 0) return null;

  return (
    <div className="text-xs">
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); toggle(); }}
        className="flex w-full items-center gap-2 text-left hover:opacity-90"
        aria-expanded={expanded}
      >
        <span className={`transition-transform ${expanded ? 'rotate-90' : ''}`}>▸</span>
        <span>⚙️ Jobs ({total})</span>
        {runningJobs.length > 0 && (
          <span className="text-emerald-600">· {runningJobs.length} running</span>
        )}
      </button>
      {expanded && (
        <ul className="mt-1 ml-3 space-y-1 border-l-2 border-[var(--border-primary)] pl-2">
          {runningJobs.map((j) => (
            <JobItem
              key={j.job_id}
              agentId={agentId}
              jobId={j.job_id}
              title={j.title}
              subtitle={j.description}
              state="running"
              extraRight={j.progress ? `step ${j.progress.current_step}/${j.progress.total_steps}` : null}
            />
          ))}
          {pendingJobs.map((j) => (
            <JobItem
              key={j.job_id}
              agentId={agentId}
              jobId={j.job_id}
              title={j.title}
              subtitle={j.description}
              state={j.queue_status ?? 'pending'}
              extraRight={j.next_run_time ? `next ${formatTime(j.next_run_time)}` : null}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch {
    return iso;
  }
}

interface JobItemProps {
  agentId: string;
  jobId: string;
  title: string;
  subtitle?: string | null;
  state: 'running' | JobQueueStatus;
  extraRight: string | null;
}

function JobItem({ agentId, jobId, title, subtitle, state, extraRight }: JobItemProps) {
  const { expanded, toggle } = useExpanded(
    `${agentId}:item:job:${jobId}`,
    false,
  );
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [action, setAction] = useState<string | null>(null);
  const meta = STATE_META[state];

  const onClick = async (e: React.MouseEvent) => {
    e.stopPropagation();
    toggle();
    if (!expanded && detail === null && !loading) {
      setLoading(true);
      try {
        const res = await api.getJobDetail(jobId);
        setDetail(res.job as Record<string, unknown>);
      } catch (e: unknown) {
        setErr(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    }
  };

  const runAction = async (e: React.MouseEvent, fn: () => Promise<unknown>, label: string) => {
    e.stopPropagation();
    setAction(label);
    try {
      await fn();
      // force refresh of detail
      setDetail(null);
      setAction(`${label} ✓`);
      setTimeout(() => setAction(null), 1500);
    } catch (err) {
      setAction(`${label} failed`);
      setTimeout(() => setAction(null), 2000);
      void err;
    }
  };

  return (
    <li className="text-[11px]">
      <button
        type="button"
        onClick={onClick}
        className="flex w-full items-center gap-2 py-0.5 text-left hover:bg-[var(--bg-tertiary)] rounded"
        aria-expanded={expanded}
      >
        <span aria-hidden>{meta.icon}</span>
        <span className="font-medium">{title}</span>
        <span className={`${meta.cls}`}>· {meta.label}</span>
        {extraRight && (
          <span className="text-[var(--text-secondary)] truncate">· {extraRight}</span>
        )}
        <span className={`ml-auto transition-transform ${expanded ? 'rotate-90' : ''}`}>▸</span>
      </button>
      {expanded && (
        <div className="ml-7 mt-1 rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-2 space-y-1.5">
          {subtitle && <div className="text-[var(--text-secondary)]">{subtitle}</div>}
          {loading && <div className="text-[var(--text-secondary)]">Loading…</div>}
          {err && <div className="text-red-500">Failed: {err}</div>}
          {detail !== null && <JobDetailBody detail={detail} />}
          <div className="flex flex-wrap gap-1.5 pt-1">
            {state === 'failed' && (
              <ActionBtn
                label={action ?? 'Retry'}
                onClick={(e) => runAction(e, () => api.retryJob(jobId), 'Retry')}
              />
            )}
            {(state === 'active' || state === 'pending') && (
              <ActionBtn
                label={action ?? 'Pause'}
                onClick={(e) => runAction(e, () => api.pauseJob(jobId), 'Pause')}
              />
            )}
            {state === 'paused' && (
              <ActionBtn
                label={action ?? 'Resume'}
                onClick={(e) => runAction(e, () => api.resumeJob(jobId), 'Resume')}
              />
            )}
          </div>
        </div>
      )}
    </li>
  );
}

function ActionBtn({ label, onClick }: { label: string; onClick: (e: React.MouseEvent) => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded bg-[var(--bg-secondary)] px-2 py-0.5 text-[10px] font-medium hover:bg-[var(--accent-primary)] hover:text-white"
    >
      {label}
    </button>
  );
}

function JobDetailBody({ detail }: { detail: Record<string, unknown> }) {
  const d = detail;
  const trigger = String(d.trigger_config ?? '(manual)');
  const nextRun = d.next_run_time ? String(d.next_run_time) : null;
  const iter = typeof d.iteration_count === 'number' ? d.iteration_count : 0;
  const lastErr = d.last_error ? String(d.last_error) : null;
  return (
    <div className="text-[var(--text-secondary)] space-y-0.5">
      {nextRun && <div>Next run: <span className="font-mono">{nextRun}</span></div>}
      {iter > 0 && <div>Iterations: {iter}</div>}
      {trigger && <div className="truncate">Trigger: <span className="font-mono">{trigger}</span></div>}
      {lastErr && (
        <div className="mt-1 rounded border border-red-500/40 bg-red-500/5 p-1.5 text-red-600">
          <div className="font-semibold">Last error</div>
          <div className="font-mono text-[10px] whitespace-pre-wrap">{lastErr}</div>
        </div>
      )}
    </div>
  );
}
