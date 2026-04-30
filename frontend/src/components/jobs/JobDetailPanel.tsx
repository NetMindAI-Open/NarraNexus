/**
 * Job Detail Panel - Display detailed information for the selected job
 */

import { X, Clock, PlayCircle, CheckCircle, XCircle, AlertCircle } from 'lucide-react';
import { Badge, Button, ScrollArea } from '@/components/ui';
import { formatRelativeTime } from '@/lib/utils';
import type { JobNode, JobNodeStatus } from '@/types/jobComplex';

interface JobDetailPanelProps {
  job: JobNode | null;
  onClose: () => void;
}

const statusConfig: Record<JobNodeStatus, { icon: typeof Clock; color: string; label: string }> = {
  pending: { icon: Clock, color: 'text-gray-500', label: 'Pending' },
  active: { icon: AlertCircle, color: 'text-blue-500', label: 'Active' },
  running: { icon: PlayCircle, color: 'text-[var(--color-yellow-500)]', label: 'Running' },
  completed: { icon: CheckCircle, color: 'text-[var(--color-green-500)]', label: 'Completed' },
  failed: { icon: XCircle, color: 'text-[var(--color-red-500)]', label: 'Failed' },
  cancelled: { icon: XCircle, color: 'text-gray-400', label: 'Cancelled' },
};

export function JobDetailPanel({ job, onClose }: JobDetailPanelProps) {
  if (!job) {
    return (
      <div className="p-4 text-center text-[var(--text-tertiary)]">
        Click a node to view details
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
        {config.label}
      </Badge>

      {/* Details Grid */}
      <div className="space-y-3 text-sm">
        <div className="grid grid-cols-[100px_1fr] gap-2">
          <span className="text-[var(--text-tertiary)]">ID:</span>
          <span className="font-mono text-[var(--text-secondary)] break-all">{job.id}</span>
        </div>

        <div className="grid grid-cols-[100px_1fr] gap-2">
          <span className="text-[var(--text-tertiary)]">Task Key:</span>
          <span className="font-mono text-[var(--text-secondary)]">{job.task_key}</span>
        </div>

        {job.description && (
          <div className="grid grid-cols-[100px_1fr] gap-2">
            <span className="text-[var(--text-tertiary)]">Description:</span>
            <span className="text-[var(--text-secondary)]">{job.description}</span>
          </div>
        )}

        {job.depends_on.length > 0 && (
          <div className="grid grid-cols-[100px_1fr] gap-2">
            <span className="text-[var(--text-tertiary)]">Depends on:</span>
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
            <span className="text-[var(--text-tertiary)]">Started:</span>
            <span className="text-[var(--text-secondary)]">
              {formatRelativeTime(job.started_at)}
            </span>
          </div>
        )}

        {job.completed_at && (
          <div className="grid grid-cols-[100px_1fr] gap-2">
            <span className="text-[var(--text-tertiary)]">Completed:</span>
            <span className="text-[var(--text-secondary)]">
              {formatRelativeTime(job.completed_at)}
            </span>
          </div>
        )}

        {job.output && (
          <div className="pt-2 border-t border-[var(--border-subtle)]">
            <span className="text-[var(--text-tertiary)] block mb-1">Output:</span>
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
