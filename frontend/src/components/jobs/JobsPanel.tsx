/**
 * Jobs Panel - Task management panel
 * Bioluminescent Terminal style - Deep ocean aesthetics
 * Enhanced with Control Center Dashboard design
 *
 * Supports three view modes:
 * 1. List view - Traditional job list
 * 2. Graph view - React Flow dependency visualization
 * 3. Timeline view - Gantt chart style execution timeline
 */

import { useState, useMemo } from 'react';
import {
  Clock,
  Play,
  Pause,
  CheckCircle,
  XCircle,
  RefreshCw,
  Calendar,
  Ban,
  List,
  GitBranch,
  GanttChartSquare,
  Zap,
  TrendingUp,
  AlertCircle,
  ChevronDown,
  ChevronRight,
} from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent, Button, Badge, StatStrip, ScrollArea, useConfirm } from '@/components/ui';
import { useConfigStore, usePreloadStore } from '@/stores';
import { cn } from '@/lib/utils';
import { api } from '@/lib/api';
import { JobDependencyGraph } from './JobDependencyGraph';
import { JobExecutionTimeline } from './JobExecutionTimeline';
import { JobDetailPanel } from './JobDetailPanel';
import { JobExpandedDetail } from './JobExpandedDetail';
import { StatusDistributionBar } from './StatusDistributionBar';
import type { JobNode, JobNodeStatus } from '@/types/jobComplex';
import type { Job } from '@/types/api';

type ViewMode = 'list' | 'graph' | 'timeline';

const statusConfig: Record<string, { icon: typeof Clock; color: string; bgColor: string; label: string }> = {
  pending: { icon: Clock, color: 'text-[var(--text-tertiary)]', bgColor: 'bg-[var(--bg-tertiary)]', label: 'Pending' },
  active: { icon: Zap, color: 'text-[var(--accent-primary)]', bgColor: 'bg-[var(--accent-glow)]', label: 'Active' },
  running: { icon: Play, color: 'text-[var(--color-warning)]', bgColor: 'bg-[var(--color-warning)]/10', label: 'Running' },
  paused: { icon: Pause, color: 'text-[var(--accent-secondary)]', bgColor: 'bg-[var(--accent-secondary)]/10', label: 'Paused' },
  completed: { icon: CheckCircle, color: 'text-[var(--color-success)]', bgColor: 'bg-[var(--color-success)]/10', label: 'Completed' },
  failed: { icon: XCircle, color: 'text-[var(--color-error)]', bgColor: 'bg-[var(--color-error)]/10', label: 'Failed' },
  cancelled: { icon: Ban, color: 'text-[var(--text-tertiary)]', bgColor: 'bg-[var(--bg-tertiary)]', label: 'Cancelled' },
};

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

export function JobsPanel() {
  const [viewMode, setViewMode] = useState<ViewMode>('list');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [cancellingJobId, setCancellingJobId] = useState<string | null>(null);
  const [failedExpanded, setFailedExpanded] = useState(false);
  const { confirm, alert, dialog: confirmDialog } = useConfirm();

  const { agentId, userId } = useConfigStore();
  const {
    jobs: allJobs,
    jobsLoading: loading,
    refreshJobs,
  } = usePreloadStore();

  // Filter Jobs
  const jobs = statusFilter === 'all'
    ? allJobs
    : allJobs.filter((job) => job.status === statusFilter);

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

  // Calculate job metrics
  const jobMetrics = useMemo(() => {
    const completed = allJobs.filter((j) => j.status === 'completed').length;
    const running = allJobs.filter((j) => j.status === 'running' || j.status === 'active').length;
    const failed = allJobs.filter((j) => j.status === 'failed').length;
    const successRate = allJobs.length > 0 ? Math.round((completed / (completed + failed || 1)) * 100) : 0;
    return { completed, running, failed, successRate };
  }, [allJobs]);

  const handleRefresh = () => {
    refreshJobs(agentId, userId);
  };

  const handleCancelJob = async (e: React.MouseEvent, jobId: string) => {
    e.stopPropagation();

    const ok = await confirm({
      title: 'Cancel job',
      message: 'Are you sure you want to cancel this job? This action cannot be undone.',
      confirmText: 'Cancel job',
      cancelText: 'Keep running',
      danger: true,
    });
    if (!ok) return;

    setCancellingJobId(jobId);
    try {
      const res = await api.cancelJob(jobId);
      if (res.success) {
        refreshJobs(agentId, userId);
      } else {
        await alert({
          title: 'Cancel failed',
          message: res.error || 'Failed to cancel job',
          danger: true,
        });
      }
    } catch (err) {
      console.error('Cancel job error:', err);
      await alert({
        title: 'Cancel failed',
        message: 'Failed to cancel job. Please try again.',
        danger: true,
      });
    } finally {
      setCancellingJobId(null);
    }
  };

  const canCancel = (status: string) => {
    return ['pending', 'active', 'running'].includes(status);
  };

  return (
    <Card variant="glass" className="flex flex-col h-full">
      {confirmDialog}
      <CardHeader>
        <CardTitle>
          <Calendar />
          Jobs
          <span className="ml-1 text-[var(--text-tertiary)] tabular-nums normal-case tracking-normal">
            · {allJobs.length}
          </span>
        </CardTitle>
        <Button
          variant="ghost"
          size="icon"
          onClick={handleRefresh}
          disabled={loading}
          title="Refresh"
        >
          <RefreshCw className={cn('w-4 h-4', loading && 'animate-spin')} />
        </Button>
      </CardHeader>

      {/* Stat strip */}
      {allJobs.length > 0 && (
        <>
          <StatStrip
            items={[
              { label: 'Active', value: jobMetrics.running, icon: Zap, tone: 'warning', pulse: jobMetrics.running > 0 },
              { label: 'Success', value: jobMetrics.completed, icon: CheckCircle, tone: 'success' },
              { label: 'Failed', value: jobMetrics.failed, icon: AlertCircle, tone: jobMetrics.failed > 0 ? 'error' : 'secondary' },
              { label: 'Rate', value: `${jobMetrics.successRate}%`, icon: TrendingUp },
            ]}
          />
          <div className="px-5 py-3">
            <StatusDistributionBar jobs={allJobs} />
          </div>
        </>
      )}

      {/* View mode + status filter — one compact row of underline tabs */}
      <div className="px-5 pt-2 pb-0 flex items-center gap-4 border-t border-[var(--rule)]">
        <div className="flex gap-4">
          {[
            { mode: 'list' as const, icon: List, label: 'List' },
            { mode: 'graph' as const, icon: GitBranch, label: 'Graph' },
            { mode: 'timeline' as const, icon: GanttChartSquare, label: 'Timeline' },
          ].map(({ mode, icon: Icon, label }) => (
            <button
              key={mode}
              onClick={() => setViewMode(mode)}
              className={cn(
                'flex items-center gap-1.5 py-2 text-[11px] font-medium font-[family-name:var(--font-mono)] uppercase tracking-[0.14em]',
                'border-b-2 -mb-px transition-colors duration-150',
                viewMode === mode
                  ? 'border-[var(--text-primary)] text-[var(--text-primary)]'
                  : 'border-transparent text-[var(--text-tertiary)] hover:text-[var(--text-primary)]'
              )}
            >
              <Icon className="w-3 h-3" />
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Status filter — thin filter row, only in list view.
          Horizontal overflow uses ScrollArea (hideScrollbar) so the row
          stays scrollable on narrow widths without showing a track. */}
      {viewMode === 'list' && (
        <ScrollArea horizontal hideScrollbar className="border-t border-[var(--rule)]">
        <div className="px-5 py-2 flex gap-1">
          {(['all', 'active', 'running', 'paused', 'pending', 'completed', 'failed', 'cancelled'] as const).map((status) => {
            const config = status !== 'all' ? statusConfig[status] : null;
            const isActive = statusFilter === status;
            return (
              <button
                key={status}
                onClick={() => setStatusFilter(status)}
                className={cn(
                  'px-2 py-1 text-[10px] whitespace-nowrap font-medium font-[family-name:var(--font-mono)] uppercase tracking-[0.12em]',
                  'transition-colors duration-150',
                  isActive
                    ? 'bg-[var(--text-primary)] text-[var(--text-inverse)]'
                    : 'text-[var(--text-tertiary)] hover:text-[var(--text-primary)]'
                )}
              >
                {status === 'all' ? 'All' : config?.label}
              </button>
            );
          })}
        </div>
        </ScrollArea>
      )}

      <CardContent className="flex-1 overflow-hidden min-h-0">
        {/* List View */}
        {viewMode === 'list' && (
          <ScrollArea className="h-full" viewportClassName="py-2"><div className="space-y-3">
            {jobs.length === 0 ? (
              <div className="h-full flex items-center justify-center">
                <div className="text-center p-8">
                  <div className="w-14 h-14 rounded-2xl bg-[var(--color-warning)]/10 mx-auto mb-4 flex items-center justify-center">
                    <Calendar className="w-7 h-7 text-[var(--color-warning)]" />
                  </div>
                  <p className="text-[var(--text-secondary)] text-sm font-medium mb-1">No jobs found</p>
                  <p className="text-[var(--text-tertiary)] text-xs">Create a job to get started</p>
                </div>
              </div>
            ) : (
              (() => {
                // In "all" mode, separate failed jobs into a collapsible group at the bottom
                const isAllMode = statusFilter === 'all';
                const mainJobs = isAllMode ? jobs.filter((j) => j.status !== 'failed') : jobs;
                const failedJobs = isAllMode ? jobs.filter((j) => j.status === 'failed') : [];

                const renderJobCard = (job: Job) => {
                  const config = statusConfig[job.status] || statusConfig.pending;
                  const StatusIcon = config.icon;
                  const isExpanded = expandedId === job.job_id;
                  const isCancelling = cancellingJobId === job.job_id;

                  return (
                    <div
                      key={job.job_id}
                      onClick={() => setExpandedId(isExpanded ? null : job.job_id)}
                      className={cn(
                        'w-full text-left p-4 transition-colors duration-150 group cursor-pointer',
                        /* Nested card pattern: parent panel sits on bg-primary,
                           so each row lifts to bg-elevated and uses a hairline
                           border-subtle. Same-color nesting (primary on primary)
                           made the 1px border read as a heavy "double frame".
                           Hover/expand escalates to border-strong only — no
                           glow, no status-tinted frames. */
                        'border bg-[var(--bg-elevated)]',
                        isExpanded
                          ? 'border-[var(--border-strong)]'
                          : 'border-[var(--border-subtle)] hover:border-[var(--border-strong)]',
                        job.status === 'cancelled' && 'opacity-60'
                      )}
                    >
                      <div className="flex items-start gap-3">
                        <div className={cn(
                          'w-8 h-8 rounded-lg flex items-center justify-center shrink-0 transition-all duration-300',
                          config.bgColor,
                          job.status === 'running' && 'animate-pulse'
                        )}>
                          <StatusIcon className={cn('w-4 h-4', config.color)} />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center justify-between gap-2">
                            <span className={cn(
                              'text-sm font-semibold truncate transition-colors',
                              job.status === 'cancelled'
                                ? 'text-[var(--text-tertiary)] line-through'
                                : 'text-[var(--text-primary)] group-hover:text-[var(--accent-primary)]'
                            )}>
                              {job.title}
                            </span>
                            <Badge
                              variant={
                                job.status === 'running'
                                  ? 'warning'
                                  : job.status === 'completed'
                                  ? 'success'
                                  : job.status === 'failed'
                                  ? 'error'
                                  : job.status === 'active'
                                  ? 'accent'
                                  : job.status === 'paused'
                                  ? 'outline'
                                  : 'default'
                              }
                              size="sm"
                              glow={job.status === 'running' || job.status === 'active'}
                            >
                              {config.label}
                            </Badge>
                          </div>

                          {job.description && (
                            <p className="text-xs text-[var(--text-tertiary)] mt-1.5 line-clamp-1">
                              {job.description}
                            </p>
                          )}

                          {isExpanded && (
                            <JobExpandedDetail
                              job={job}
                              isCancelling={isCancelling}
                              canCancel={canCancel(job.status)}
                              onCancel={handleCancelJob}
                            />
                          )}
                        </div>
                      </div>
                    </div>
                  );
                };

                return (
                  <>
                    {mainJobs.map(renderJobCard)}

                    {/* Failed jobs collapsible group */}
                    {failedJobs.length > 0 && (
                      <div className="mt-1">
                        <button
                          onClick={() => setFailedExpanded(!failedExpanded)}
                          className={cn(
                            'w-full flex items-center gap-2 px-3 py-2 rounded-lg transition-all',
                            'text-[var(--text-tertiary)] hover:text-[var(--text-secondary)] hover:bg-[var(--bg-elevated)]',
                          )}
                        >
                          {failedExpanded ? (
                            <ChevronDown className="w-3.5 h-3.5" />
                          ) : (
                            <ChevronRight className="w-3.5 h-3.5" />
                          )}
                          <XCircle className="w-3.5 h-3.5 text-[var(--color-error)]" />
                          <span className="text-xs font-medium">
                            {failedJobs.length} failed job{failedJobs.length !== 1 ? 's' : ''}
                          </span>
                        </button>
                        {failedExpanded && (
                          <div className="space-y-3 mt-2">
                            {failedJobs.map(renderJobCard)}
                          </div>
                        )}
                      </div>
                    )}
                  </>
                );
              })()
            )}
          </div></ScrollArea>
        )}

        {/* Graph View */}
        {viewMode === 'graph' && (
          <div className="h-full flex flex-col">
            {!hasJobsWithDependencies ? (
              <div className="flex-1 flex flex-col items-center justify-center text-center p-8">
                <div className="w-16 h-16 rounded-2xl bg-[var(--accent-secondary)]/10 mx-auto mb-4 flex items-center justify-center">
                  <GitBranch className="w-8 h-8 text-[var(--accent-secondary)]" />
                </div>
                <p className="text-[var(--text-secondary)] text-sm font-medium mb-1">
                  No dependency relationships
                </p>
                <p className="text-[var(--text-tertiary)] text-xs">
                  Jobs with dependencies will be visualized here
                </p>
              </div>
            ) : (
              <>
                <div className="flex-1 min-h-[300px] rounded-xl overflow-hidden border border-[var(--border-subtle)]">
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
              <div className="flex-1 flex items-center justify-center">
                <div className="text-center p-8">
                  <div className="w-16 h-16 rounded-2xl bg-[var(--accent-primary)]/10 mx-auto mb-4 flex items-center justify-center">
                    <GanttChartSquare className="w-8 h-8 text-[var(--accent-primary)]" />
                  </div>
                  <p className="text-[var(--text-secondary)] text-sm font-medium mb-1">No jobs to display</p>
                  <p className="text-[var(--text-tertiary)] text-xs">Create jobs to see the timeline</p>
                </div>
              </div>
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
    </Card>
  );
}

export default JobsPanel;
