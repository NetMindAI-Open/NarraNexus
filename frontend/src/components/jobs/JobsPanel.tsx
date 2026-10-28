/**
 * @file_name: JobsPanel.tsx
 * @author:
 * @date: 2026-08-27
 * @description: Root orchestrator for the Jobs panel (list / graph / timeline).
 *
 * Density contract (2026-08-27 rebuild): a band renders only when the data it
 * carries is non-empty. The panel used to open with six unconditional bands —
 * a refresh-only toolbar, a four-tile stat strip, a distribution bar, a view
 * tab row and an eleven-chip filter row — roughly 354px of chrome above the
 * first job, of which ~289px was zero-information for a typical agent (four
 * zeroes, a single-color bar, and ten filters whose only possible outcome was
 * an empty list). Every "which bands exist" rule now lives in
 * `lib/jobsPanelModel.ts` so it can be tested.
 */

import { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Calendar,
  RefreshCw,
  List,
  GitBranch,
  GanttChartSquare,
  ChevronDown,
  ChevronRight,
} from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent, Button, ScrollArea, useConfirm } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { BracketEmptyState } from '@/components/nm';
import { useConfigStore, usePreloadStore } from '@/stores';
import { cn } from '@/lib/utils';
import { api } from '@/lib/api';
import { filterOptions } from '@/lib/jobsPanelModel';
import { JobDependencyGraph } from './JobDependencyGraph';
import { JobExecutionTimeline } from './JobExecutionTimeline';
import { JobDetailPanel } from './JobDetailPanel';
import { JobExpandedDetail } from './JobExpandedDetail';
import { JobScheduleEditDialog } from './JobScheduleEditDialog';
import { JobPayloadEditDialog } from './JobPayloadEditDialog';
import { JobStatusMeter } from './JobStatusMeter';
import { JobRow } from './JobRow';
import { statusVisual } from './jobStatusVisuals';
import type { JobNode, JobNodeStatus } from '@/types/jobComplex';
import type { Job } from '@/types/api';

type ViewMode = 'list' | 'graph' | 'timeline';

interface JobsPanelProps {
  /** When true, skips the outer Card chrome so the panel can be embedded
   *  inside another container (e.g. ActivityPanel in the bookmark drawer).
   *  All functional behaviour is unchanged; default=false preserves existing
   *  call sites. */
  embedded?: boolean;
  /** Fired after a job is successfully cancelled or resumed — lets the
   *  bookmark layer resolve a pending 'attention' state for that job. */
  onJobResolved?: (jobId: string) => void;
}

// Convert API Job to JobNode format
function jobToJobNode(job: Job): JobNode {
  // Prefer API-returned depends_on, fall back to parsing from payload
  let depends_on: string[] = job.depends_on || [];

  // If API did not return depends_on, try parsing from payload (backward compatibility)
  if (depends_on.length === 0 && job.payload) {
    try {
      const payload = JSON.parse(job.payload);
      if (payload.depends_on && Array.isArray(payload.depends_on)) {
        depends_on = payload.depends_on;
      }
    } catch {
      // Ignore parsing errors
    }
  }

  return {
    id: job.instance_id || job.job_id,  // Use instance_id as node ID (matches dependency relations)
    task_key: job.instance_id || job.job_id,
    title: job.title,
    description: job.description,
    status: job.status as JobNodeStatus,
    depends_on,
    started_at: job.last_run_at,
    completed_at: job.status === 'completed' ? job.updated_at : undefined,
    output: job.process?.join('\n'),
  };
}

const VIEW_MODES = [
  { mode: 'list' as const, icon: List, labelKey: 'jobs.view.list' },
  { mode: 'graph' as const, icon: GitBranch, labelKey: 'jobs.view.graph' },
  { mode: 'timeline' as const, icon: GanttChartSquare, labelKey: 'jobs.view.timeline' },
];

export function JobsPanel({ embedded = false, onJobResolved }: JobsPanelProps = {}) {
  const { t } = useTranslation();
  const [viewMode, setViewMode] = useState<ViewMode>('list');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [cancellingJobId, setCancellingJobId] = useState<string | null>(null);
  const [resumingJobId, setResumingJobId] = useState<string | null>(null);
  const [pausingJobId, setPausingJobId] = useState<string | null>(null);
  const [editingJob, setEditingJob] = useState<Job | null>(null);
  const [savingSchedule, setSavingSchedule] = useState(false);
  const [editingPayloadJob, setEditingPayloadJob] = useState<Job | null>(null);
  const [savingPayload, setSavingPayload] = useState(false);
  const [failedExpanded, setFailedExpanded] = useState(false);
  const { confirm, alert, dialog: confirmDialog } = useConfirm();

  const { agentId, userId } = useConfigStore();
  const {
    jobs: allJobs,
    jobsLoading: loading,
    refreshJobs,
  } = usePreloadStore();

  // Chips are derived from the data, so the set changes as jobs finish. If the
  // selected filter's chip disappears (its last job resolved), fall back to
  // 'all' rather than stranding the user on an empty list with no visible
  // filter to clear — derived instead of an effect so there is no flash frame.
  const options = useMemo(() => filterOptions(allJobs), [allJobs]);
  const activeFilter = options.some((o) => o.status === statusFilter) ? statusFilter : 'all';

  // Filter Jobs
  const jobs = activeFilter === 'all'
    ? allJobs
    : allJobs.filter((job) => job.status === activeFilter);

  // Convert to JobNode format (for graph and timeline)
  const jobNodes: JobNode[] = useMemo(() => jobs.map(jobToJobNode), [jobs]);

  // Get selected job details
  const selectedJob = useMemo(
    () => jobNodes.find((j) => j.id === selectedJobId) || null,
    [jobNodes, selectedJobId]
  );

  // Check if any jobs have dependencies
  const hasJobsWithDependencies = useMemo(
    () => jobNodes.some((j) => j.depends_on.length > 0),
    [jobNodes]
  );

  const handleRefresh = () => {
    refreshJobs(agentId, userId);
  };

  const handleCancelJob = async (e: React.MouseEvent, jobId: string) => {
    e.stopPropagation();

    const ok = await confirm({
      title: t('jobs.cancel.title'),
      message: t('jobs.cancel.message'),
      confirmText: t('jobs.cancel.confirm'),
      cancelText: t('jobs.cancel.keepRunning'),
      danger: true,
    });
    if (!ok) return;

    setCancellingJobId(jobId);
    try {
      const res = await api.cancelJob(jobId);
      if (res.success) {
        onJobResolved?.(jobId);
        refreshJobs(agentId, userId);
      } else {
        await alert({
          title: t('jobs.cancel.failedTitle'),
          message: res.error || t('jobs.cancel.failedMessage'),
          danger: true,
        });
      }
    } catch (err) {
      console.error('Cancel job error:', err);
      await alert({
        title: t('jobs.cancel.failedTitle'),
        message: t('jobs.cancel.failedRetry'),
        danger: true,
      });
    } finally {
      setCancellingJobId(null);
    }
  };

  const canCancel = (status: string) => {
    return ['pending', 'active', 'running'].includes(status);
  };

  const canResume = (status: string) => {
    return ['paused', 'paused_no_quota', 'paused_spend_cap', 'cooling', 'blocked_failed'].includes(status);
  };

  const canPause = (status: string) => {
    return ['active', 'pending'].includes(status);
  };

  // Execution time is editable for any non-running, non-terminal job — mirrors
  // the backend reschedule_job guard (_NON_EDITABLE_STATUSES).
  const canEdit = (status: string) => {
    return !['running', 'completed', 'cancelled', 'failed'].includes(status);
  };

  const handleEditSchedule = (e: React.MouseEvent, job: Job) => {
    e.stopPropagation();
    setEditingJob(job);
  };

  const handleSaveSchedule = async (
    fields: { run_at?: string; cron?: string; interval_seconds?: number; timezone?: string },
  ) => {
    if (!editingJob) return;
    setSavingSchedule(true);
    try {
      await api.updateJobSchedule(editingJob.job_id, fields);
      setEditingJob(null);
      refreshJobs(agentId, userId);
    } catch (err) {
      console.error('Reschedule job error:', err);
      await alert({
        title: t('jobs.editSchedule.failedTitle'),
        message: err instanceof Error ? err.message : t('jobs.editSchedule.failedMessage'),
        danger: true,
      });
    } finally {
      setSavingSchedule(false);
    }
  };

  const handleEditPayload = (e: React.MouseEvent, job: Job) => {
    e.stopPropagation();
    setEditingPayloadJob(job);
  };

  const handleSavePayload = async (
    fields: { title?: string; description?: string; payload?: string },
  ) => {
    if (!editingPayloadJob) return;
    setSavingPayload(true);
    try {
      const res = await api.updateJob(editingPayloadJob.job_id, editingPayloadJob.agent_id, fields);
      if (!res.success) {
        await alert({
          title: t('jobs.editPayload.failedTitle'),
          message: res.message || t('jobs.editPayload.failedMessage'),
          danger: true,
        });
        return;
      }
      setEditingPayloadJob(null);
      refreshJobs(agentId, userId);
    } catch (err) {
      console.error('Update job payload error:', err);
      await alert({
        title: t('jobs.editPayload.failedTitle'),
        message: err instanceof Error ? err.message : t('jobs.editPayload.failedMessage'),
        danger: true,
      });
    } finally {
      setSavingPayload(false);
    }
  };

  const handlePauseJob = async (e: React.MouseEvent, jobId: string) => {
    e.stopPropagation();
    setPausingJobId(jobId);
    try {
      const res = await api.pauseJob(jobId);
      if (res.success) {
        refreshJobs(agentId, userId);
      } else {
        await alert({ title: t('jobs.pause.failedTitle'), message: t('jobs.pause.failedMessage'), danger: true });
      }
    } catch (err) {
      console.error('Pause job error:', err);
      await alert({ title: t('jobs.pause.failedTitle'), message: t('jobs.pause.failedRetry'), danger: true });
    } finally {
      setPausingJobId(null);
    }
  };

  const handleResumeJob = async (e: React.MouseEvent, jobId: string) => {
    e.stopPropagation();
    setResumingJobId(jobId);
    try {
      const res = await api.resumeJob(jobId);
      if (res.success) {
        onJobResolved?.(jobId);
        refreshJobs(agentId, userId);
      } else {
        await alert({
          title: t('jobs.resume.failedTitle'),
          message: t('jobs.resume.failedMessage'),
          danger: true,
        });
      }
    } catch (err) {
      console.error('Resume job error:', err);
      await alert({
        title: t('jobs.resume.failedTitle'),
        message: t('jobs.resume.failedRetry'),
        danger: true,
      });
    } finally {
      setResumingJobId(null);
    }
  };

  const refreshButton = (
    <Button
      variant="ghost"
      size="icon"
      onClick={handleRefresh}
      disabled={loading}
      title={t('jobs.refresh')}
      aria-label={t('jobs.refresh')}
    >
      <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />
    </Button>
  );

  const renderJobRow = (job: Job) => (
    <JobRow
      key={job.job_id}
      job={job}
      expanded={expandedId === job.job_id}
      onToggle={() => setExpandedId(expandedId === job.job_id ? null : job.job_id)}
    >
      <JobExpandedDetail
        job={job}
        isCancelling={cancellingJobId === job.job_id}
        canCancel={canCancel(job.status)}
        onCancel={handleCancelJob}
        canResume={canResume(job.status)}
        isResuming={resumingJobId === job.job_id}
        onResume={handleResumeJob}
        canPause={canPause(job.status)}
        isPausing={pausingJobId === job.job_id}
        onPause={handlePauseJob}
        canEdit={canEdit(job.status)}
        onEdit={handleEditSchedule}
        onEditPayload={handleEditPayload}
      />
    </JobRow>
  );

  const inner = (
    <>
      {confirmDialog}
      {editingJob && (
        <JobScheduleEditDialog
          job={editingJob}
          isOpen={!!editingJob}
          saving={savingSchedule}
          onClose={() => setEditingJob(null)}
          onSave={handleSaveSchedule}
        />
      )}
      {editingPayloadJob && (
        <JobPayloadEditDialog
          job={editingPayloadJob}
          isOpen={!!editingPayloadJob}
          saving={savingPayload}
          onClose={() => setEditingPayloadJob(null)}
          onSave={handleSavePayload}
        />
      )}

      {/* Embedded mode has no header of its own: the drawer shell already names
          the panel, and the lone refresh action moved into the controls row
          below (it used to occupy a full band by itself). */}
      {!embedded && (
        <CardHeader>
          <CardTitle>
            <Calendar />
            {t('jobs.title')}
            <span className="ml-1 text-[var(--text-tertiary)] tabular-nums normal-case tracking-normal">
              · {allJobs.length}
            </span>
          </CardTitle>
          {refreshButton}
        </CardHeader>
      )}

      {/* Band B — renders only when there is a distribution worth drawing. */}
      <JobStatusMeter jobs={allJobs} />

      {/* Band C — status filter + view switch + (embedded) refresh, one row.
          This row sits OUTSIDE the scroll viewport, so it stays visible for a
          200-job list without needing sticky positioning. */}
      <div className="px-3.5 py-1.5 flex items-center gap-2 border-b border-[var(--rule)]">
        {viewMode === 'list' && options.length > 0 && (
          <div className="flex flex-wrap items-center gap-0.5 min-w-0">
            {options.map(({ status, count }) => {
              const isActive = activeFilter === status;
              const label = status === 'all'
                ? t('jobs.filter.all')
                : t(statusVisual(status).labelKey);
              return (
                <button
                  key={status}
                  onClick={() => setStatusFilter(status)}
                  aria-pressed={isActive}
                  // Without this the label and the count are adjacent inline
                  // spans, so the computed accessible name is "All3".
                  aria-label={`${label} ${count}`}
                  className={cn(
                    'inline-flex items-baseline gap-1.5 px-1.5 py-1 rounded-[var(--radius-xs)]',
                    'text-[10px] whitespace-nowrap font-medium font-[family-name:var(--font-mono)]',
                    'uppercase tracking-[0.11em] transition-colors duration-150',
                    isActive
                      ? 'bg-[var(--text-primary)] text-[var(--text-inverse)]'
                      : 'text-[var(--text-tertiary)] hover:text-[var(--text-primary)]',
                  )}
                >
                  {label}
                  <span
                    className={cn(
                      'tabular-nums tracking-normal',
                      isActive ? 'opacity-60' : 'text-[var(--nm-ink30)]',
                    )}
                  >
                    {count}
                  </span>
                </button>
              );
            })}
          </div>
        )}

        <div className="ml-auto shrink-0 flex items-center gap-1">
          <TooltipProvider delayDuration={200}>
            <div className="flex gap-px p-0.5 rounded-[var(--radius-sm)] bg-[var(--nm-paper-warm)]">
              {VIEW_MODES.map(({ mode, icon: Icon, labelKey }) => (
                <Tooltip key={mode}>
                  <TooltipTrigger asChild>
                    <button
                      onClick={() => setViewMode(mode)}
                      aria-label={t(labelKey)}
                      aria-pressed={viewMode === mode}
                      className={cn(
                        'w-[25px] h-[21px] flex items-center justify-center',
                        'rounded-[var(--radius-xs)] transition-colors duration-150',
                        viewMode === mode
                          ? 'bg-[var(--nm-raised)] text-[var(--text-primary)] shadow-[var(--nm-elev-1)]'
                          : 'text-[var(--text-tertiary)] hover:text-[var(--text-primary)]',
                      )}
                    >
                      <Icon className="w-3 h-3" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent>{t(labelKey)}</TooltipContent>
                </Tooltip>
              ))}
            </div>
          </TooltipProvider>
          {embedded && refreshButton}
        </div>
      </div>

      <CardContent className="flex-1 overflow-hidden min-h-0">
        {/* List View */}
        {viewMode === 'list' && (
          <ScrollArea className="h-full">
            {jobs.length === 0 ? (
              <BracketEmptyState
                label={t('jobs.empty.list.title')}
                hint={t('jobs.empty.list.hint')}
              />
            ) : (
              (() => {
                // In "all" mode, separate failed jobs into a collapsible group at the bottom
                const isAllMode = activeFilter === 'all';
                const mainJobs = isAllMode ? jobs.filter((j) => j.status !== 'failed') : jobs;
                const failedJobs = isAllMode ? jobs.filter((j) => j.status === 'failed') : [];

                return (
                  <>
                    {mainJobs.map(renderJobRow)}

                    {/* Failed jobs collapsible group. The `Failed n` filter chip
                        is the second route to the same set — failures are
                        reachable both by scanning and by filtering, instead of
                        living only at the bottom of the list. */}
                    {failedJobs.length > 0 && (
                      <>
                        <button
                          onClick={() => setFailedExpanded(!failedExpanded)}
                          aria-expanded={failedExpanded}
                          className={cn(
                            'w-full flex items-center gap-1.5 px-3.5 py-2 transition-colors duration-150',
                            'border-t border-[var(--rule)]',
                            'text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.12em]',
                            'text-[var(--color-error)] hover:bg-[var(--nm-row-hover)]',
                          )}
                        >
                          {failedExpanded ? (
                            <ChevronDown className="w-3 h-3" />
                          ) : (
                            <ChevronRight className="w-3 h-3" />
                          )}
                          {t('jobs.failedGroup', { count: failedJobs.length })}
                        </button>
                        {failedExpanded && failedJobs.map(renderJobRow)}
                      </>
                    )}
                  </>
                );
              })()
            )}
          </ScrollArea>
        )}

        {/* Graph View */}
        {viewMode === 'graph' && (
          <div className="h-full flex flex-col">
            {!hasJobsWithDependencies ? (
              <BracketEmptyState
                className="flex-1"
                label={t('jobs.empty.graph.title')}
                hint={t('jobs.empty.graph.hint')}
              />
            ) : (
              <>
                <div className="flex-1 min-h-[300px] rounded-[var(--radius-md)] overflow-hidden border border-[var(--border-subtle)]">
                  <JobDependencyGraph
                    jobs={jobNodes}
                    onNodeClick={setSelectedJobId}
                    selectedJobId={selectedJobId}
                  />
                </div>
                {selectedJob && (
                  <div className="border-t border-[var(--border-subtle)] mt-3">
                    <JobDetailPanel
                      job={selectedJob}
                      onClose={() => setSelectedJobId(null)}
                    />
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* Timeline View */}
        {viewMode === 'timeline' && (
          <div className="h-full flex flex-col">
            {jobs.length === 0 ? (
              <BracketEmptyState
                className="flex-1"
                label={t('jobs.empty.timeline.title')}
                hint={t('jobs.empty.timeline.hint')}
              />
            ) : (
              <>
                <ScrollArea className="flex-1" viewportClassName="p-2">
                  <JobExecutionTimeline
                    jobs={jobNodes}
                    onJobClick={setSelectedJobId}
                    selectedJobId={selectedJobId}
                  />
                </ScrollArea>
                {selectedJob && (
                  <div className="border-t border-[var(--border-subtle)] mt-2">
                    <JobDetailPanel
                      job={selectedJob}
                      onClose={() => setSelectedJobId(null)}
                    />
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </CardContent>
    </>
  );

  if (embedded) {
    return <div className="flex flex-col h-full">{inner}</div>;
  }

  return (
    <Card variant="glass" className="flex flex-col h-full">
      {inner}
    </Card>
  );
}

export default JobsPanel;
