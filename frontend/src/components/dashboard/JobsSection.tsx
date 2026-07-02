/**
 * @file_name: JobsSection.tsx
 * @description: v2.1 — collapsible "Jobs" section listing all live-state jobs
 * with state-specific visuals. Item-level expand loads full job detail.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
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
  { icon: string; labelKey: string; cls: string }
> = {
  running:  { icon: '⚙️', labelKey: 'running', cls: 'text-[var(--color-green-500)]' },
  active:   { icon: '🔵', labelKey: 'active',  cls: 'text-sky-600' },
  pending:  { icon: '⚪️', labelKey: 'pending', cls: 'text-gray-500' },
  blocked:  { icon: '🟠', labelKey: 'blocked', cls: 'text-[var(--color-yellow-500)]' },
  paused:   { icon: '🟡', labelKey: 'paused',  cls: 'text-[var(--color-yellow-500)]' },
  failed:   { icon: '🔴', labelKey: 'failed',  cls: 'text-[var(--color-red-500)]' },
  cooling:         { icon: '🕒', labelKey: 'retrying',  cls: 'text-[var(--color-yellow-500)]' },
  paused_no_quota: { icon: '🟡', labelKey: 'noQuota',  cls: 'text-[var(--color-yellow-500)]' },
  blocked_failed:  { icon: '🔴', labelKey: 'depFailed', cls: 'text-[var(--color-red-500)]' },
};

export function JobsSection({ agentId, runningJobs, pendingJobs }: Props) {
  const { t } = useTranslation();
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
        <span>⚙️ {t('dashboard.jobs.title', { count: total })}</span>
        {runningJobs.length > 0 && (
          <span className="text-[var(--color-green-500)]">· {t('dashboard.jobs.running', { count: runningJobs.length })}</span>
        )}
      </button>
      {expanded && (
        <ul className="mt-1 ml-3 space-y-1 border-l-2 border-[var(--rule)] pl-2">
          {runningJobs.map((j) => (
            <JobItem
              key={j.job_id}
              agentId={agentId}
              jobId={j.job_id}
              title={j.title}
              subtitle={j.description}
              state="running"
              extraRight={j.progress ? t('dashboard.jobs.step', { current: j.progress.current_step, total: j.progress.total_steps }) : null}
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
              extraRight={j.next_run_at ? t('dashboard.jobs.next', { time: `${j.next_run_at}${j.next_run_timezone ? ` (${j.next_run_timezone})` : ''}` }) : null}
            />
          ))}
        </ul>
      )}
    </div>
  );
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
  const { t } = useTranslation();
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

  const runAction = async (
    e: React.MouseEvent,
    fn: () => Promise<unknown>,
    actionKey: 'retry' | 'pause' | 'resume',
  ) => {
    e.stopPropagation();
    const label = t(`dashboard.jobs.action.${actionKey}`);
    setAction(label);
    try {
      await fn();
      // force refresh of detail
      setDetail(null);
      setAction(`${label} ✓`);
      setTimeout(() => setAction(null), 1500);
    } catch (err) {
      setAction(t('dashboard.jobs.action.failed', { label }));
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
        <span className={`${meta.cls}`}>· {t(`dashboard.jobStateLabel.${meta.labelKey}`)}</span>
        {extraRight && (
          <span className="text-[var(--text-secondary)] truncate">· {extraRight}</span>
        )}
        <span className={`ml-auto transition-transform ${expanded ? 'rotate-90' : ''}`}>▸</span>
      </button>
      {expanded && (
        <div className="ml-7 mt-1 rounded border border-[var(--border-subtle)] bg-[var(--bg-sunken)] p-2 space-y-1.5">
          {subtitle && <div className="text-[var(--text-secondary)]">{subtitle}</div>}
          {loading && <div className="text-[var(--text-secondary)]">{t('dashboard.common.loading')}</div>}
          {err && <div className="text-[var(--color-red-500)]">{t('dashboard.common.failed')}: {err}</div>}
          {detail !== null && <JobDetailBody detail={detail} />}
          <div className="flex flex-wrap gap-1.5 pt-1">
            {state === 'failed' && (
              <ActionBtn
                label={action ?? t('dashboard.jobs.action.retry')}
                onClick={(e) => runAction(e, () => api.retryJob(jobId), 'retry')}
              />
            )}
            {(state === 'active' || state === 'pending') && (
              <ActionBtn
                label={action ?? t('dashboard.jobs.action.pause')}
                onClick={(e) => runAction(e, () => api.pauseJob(jobId), 'pause')}
              />
            )}
            {(state === 'paused' || state === 'paused_no_quota' || state === 'cooling' || state === 'blocked_failed') && (
              <ActionBtn
                label={action ?? t('dashboard.jobs.action.resume')}
                onClick={(e) => runAction(e, () => api.resumeJob(jobId), 'resume')}
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
      className="rounded bg-[var(--bg-secondary)] px-2 py-0.5 text-[10px] font-medium hover:bg-[var(--text-primary)] hover:text-[var(--text-inverse)]"
    >
      {label}
    </button>
  );
}

function JobDetailBody({ detail }: { detail: Record<string, unknown> }) {
  const { t } = useTranslation();
  const d = detail;
  const trigger = String(d.trigger_config ?? t('dashboard.jobs.detail.manual'));
  const nextRun = d.next_run_at ? String(d.next_run_at) : null;
  const nextRunTz = d.next_run_timezone ? String(d.next_run_timezone) : null;
  const iter = typeof d.iteration_count === 'number' ? d.iteration_count : 0;
  const lastErr = d.last_error ? String(d.last_error) : null;
  return (
    <div className="text-[var(--text-secondary)] space-y-0.5">
      {nextRun && <div>{t('dashboard.jobs.detail.nextRun')}: <span className="font-mono">{nextRun}{nextRunTz ? ` (${nextRunTz})` : ''}</span></div>}
      {iter > 0 && <div>{t('dashboard.jobs.detail.iterations')}: {iter}</div>}
      {trigger && <div className="truncate">{t('dashboard.jobs.detail.trigger')}: <span className="font-mono">{trigger}</span></div>}
      {lastErr && (
        <div className="mt-1 rounded border border-[var(--color-red-500)] bg-[var(--color-red-500)]/5 p-1.5 text-[var(--color-red-500)]">
          <div className="font-semibold">{t('dashboard.jobs.detail.lastError')}</div>
          <div className="font-mono text-[10px] whitespace-pre-wrap">{lastErr}</div>
        </div>
      )}
    </div>
  );
}
