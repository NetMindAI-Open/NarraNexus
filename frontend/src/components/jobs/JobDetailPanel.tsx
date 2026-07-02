/**
 * Job Detail Panel - Display detailed information for the selected job
 */

import { useTranslation } from 'react-i18next';
import { X, Clock, PlayCircle, CheckCircle, XCircle, AlertCircle } from 'lucide-react';
import { Badge, Button, ScrollArea } from '@/components/ui';
import { formatRelativeTime } from '@/lib/utils';
import type { JobNode, JobNodeStatus } from '@/types/jobComplex';

interface JobDetailPanelProps {
  job: JobNode | null;
  onClose: () => void;
}

const statusConfig: Record<JobNodeStatus, { icon: typeof Clock; color: string; labelKey: string }> = {
  pending: { icon: Clock, color: 'text-gray-500', labelKey: 'jobs.status.pending' },
  active: { icon: AlertCircle, color: 'text-blue-500', labelKey: 'jobs.status.active' },
  running: { icon: PlayCircle, color: 'text-[var(--color-yellow-500)]', labelKey: 'jobs.status.running' },
  completed: { icon: CheckCircle, color: 'text-[var(--color-green-500)]', labelKey: 'jobs.status.completed' },
  failed: { icon: XCircle, color: 'text-[var(--color-red-500)]', labelKey: 'jobs.status.failed' },
  cancelled: { icon: XCircle, color: 'text-gray-400', labelKey: 'jobs.status.cancelled' },
};

export function JobDetailPanel({ job, onClose }: JobDetailPanelProps) {
  const { t } = useTranslation();
  if (!job) {
    return (
      <div className="p-4 text-center text-[var(--text-tertiary)]">
        {t('jobs.detail.clickNode')}
      </div>
    );
  }

  const config = statusConfig[job.status];
  const StatusIcon = config.icon;

  return (
    <div className="p-4 space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2">
          <StatusIcon className={`w-5 h-5 ${config.color}`} />
          <h3 className="font-medium text-[var(--text-primary)]">{job.title}</h3>
        </div>
        <Button variant="ghost" size="icon" onClick={onClose}>
          <X className="w-4 h-4" />
        </Button>
      </div>

      {/* Status Badge */}
      <Badge
        variant={
          job.status === 'completed'
            ? 'success'
            : job.status === 'failed'
            ? 'error'
            : job.status === 'running'
            ? 'warning'
            : 'default'
        }
      >
        {t(config.labelKey)}
      </Badge>

      {/* Details Grid */}
      <div className="space-y-3 text-sm">
        <div className="grid grid-cols-[100px_1fr] gap-2">
          <span className="text-[var(--text-tertiary)]">{t('jobs.detail.id')}</span>
          <span className="font-mono text-[var(--text-secondary)] break-all">{job.id}</span>
        </div>

        <div className="grid grid-cols-[100px_1fr] gap-2">
          <span className="text-[var(--text-tertiary)]">{t('jobs.detail.taskKey')}</span>
          <span className="font-mono text-[var(--text-secondary)]">{job.task_key}</span>
        </div>

        {job.description && (
          <div className="grid grid-cols-[100px_1fr] gap-2">
            <span className="text-[var(--text-tertiary)]">{t('jobs.detail.description')}</span>
            <span className="text-[var(--text-secondary)]">{job.description}</span>
          </div>
        )}

        {job.depends_on.length > 0 && (
          <div className="grid grid-cols-[100px_1fr] gap-2">
            <span className="text-[var(--text-tertiary)]">{t('jobs.detail.dependsOn')}</span>
            <div className="flex flex-wrap gap-1">
              {job.depends_on.map((dep) => (
                <Badge key={dep} variant="default" size="sm">
                  {dep}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {job.started_at && (
          <div className="grid grid-cols-[100px_1fr] gap-2">
            <span className="text-[var(--text-tertiary)]">{t('jobs.detail.started')}</span>
            <span className="text-[var(--text-secondary)]">
              {formatRelativeTime(job.started_at)}
            </span>
          </div>
        )}

        {job.completed_at && (
          <div className="grid grid-cols-[100px_1fr] gap-2">
            <span className="text-[var(--text-tertiary)]">{t('jobs.detail.completed')}</span>
            <span className="text-[var(--text-secondary)]">
              {formatRelativeTime(job.completed_at)}
            </span>
          </div>
        )}

        {job.output && (
          <div className="pt-2 border-t border-[var(--border-subtle)]">
            <span className="text-[var(--text-tertiary)] block mb-1">{t('jobs.detail.output')}</span>
            <ScrollArea className="max-h-32 rounded bg-[var(--bg-tertiary)]" viewportClassName="p-2">
              <div className="text-[var(--text-secondary)] text-xs whitespace-pre-wrap">
                {job.output}
              </div>
            </ScrollArea>
          </div>
        )}
      </div>
    </div>
  );
}
