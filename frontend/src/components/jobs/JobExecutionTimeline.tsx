/**
 * Job Execution Timeline - Display job execution timeline
 *
 * Features:
 * 1. Gantt chart style timeline display
 * 2. Parallel jobs shown on the same row
 * 3. Color-coded by status
 * 4. Hover to show details
 */

import { useMemo } from 'react';
import { cn } from '@/lib/utils';
import type { JobNode, JobNodeStatus } from '@/types/jobComplex';

interface JobExecutionTimelineProps {
  jobs: JobNode[];
  onJobClick?: (jobId: string) => void;
  selectedJobId?: string | null;
}

// Status colors (Tailwind classes)
const statusColorClasses: Record<JobNodeStatus, string> = {
  pending: 'bg-gray-300',
  active: 'bg-blue-400',
  running: 'bg-[var(--color-yellow-500)] animate-pulse',
  completed: 'bg-green-500',
  failed: 'bg-[var(--color-red-500)]',
  cancelled: 'bg-gray-400',
};

// Parse time
function parseTime(timeStr?: string): number | null {
  if (!timeStr) return null;
  return new Date(timeStr).getTime();
}

export function JobExecutionTimeline({ jobs, onJobClick, selectedJobId }: JobExecutionTimelineProps) {
  // Calculate time range and position for each job
  const { timelineData, minTime, maxTime, formatTime } = useMemo(() => {
    // Safety check: ensure jobs is a valid array
    if (!Array.isArray(jobs) || jobs.length === 0) {
      return {
        timelineData: [],
        minTime: Date.now(),
        maxTime: Date.now(),
        formatTime: () => '',
      };
    }

    try {
      // Collect all valid timestamps
      const times: number[] = [];
      jobs.forEach((job) => {
        const start = parseTime(job.started_at);
        const end = parseTime(job.completed_at);
        if (start && !isNaN(start)) times.push(start);
        if (end && !isNaN(end)) times.push(end);
      });

      // If no time data available, use current time
      if (times.length === 0) {
        const now = Date.now();
        return {
          timelineData: jobs.map((job) => ({
            ...job,
            startPercent: 0,
            widthPercent: Math.max(100 / jobs.length, 5),
            hasTimeData: false,
          })),
          minTime: now,
          maxTime: now,
          formatTime: () => '',
        };
      }

      const min = Math.min(...times);
      const max = Math.max(...times);
      const range = max - min || 1; // Avoid division by zero

      const data = jobs.map((job) => {
        const start = parseTime(job.started_at);
        const end = parseTime(job.completed_at);

        if (!start || isNaN(start)) {
          // Job not yet started
          return {
            ...job,
            startPercent: 100,
            widthPercent: 0,
            hasTimeData: false,
          };
        }

        const startPercent = ((start - min) / range) * 100;
        const endTime = end && !isNaN(end) ? end : Date.now();
        const widthPercent = Math.max(((endTime - start) / range) * 100, 2); // Minimum 2% width

        return {
          ...job,
          startPercent: Math.max(0, Math.min(100, startPercent)),
          widthPercent: Math.max(0, Math.min(100, widthPercent)),
          hasTimeData: true,
        };
      });

      // Time formatting function
      const format = (timestamp: number) => {
        try {
          if (!timestamp || isNaN(timestamp)) return '';
          const date = new Date(timestamp);
          return date.toLocaleTimeString('en-US', {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
          });
        } catch {
          return '';
        }
      };

      return { timelineData: data, minTime: min, maxTime: max, formatTime: format };
    } catch (error) {
      console.error('Error calculating timeline:', error);
      return {
        timelineData: [],
        minTime: Date.now(),
        maxTime: Date.now(),
        formatTime: () => '',
      };
    }
  }, [jobs]);

  if (jobs.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-[var(--text-tertiary)]">
        No jobs to display
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Timeline header */}
      <div className="flex items-center justify-between text-xs text-[var(--text-tertiary)] px-28">
        <span>{formatTime(minTime)}</span>
        <span>{formatTime(maxTime)}</span>
      </div>

      {/* Job timeline list */}
      <div className="space-y-2">
        {timelineData.map((job) => (
          <div
            key={job.id}
            className={cn(
              'flex items-center gap-2 cursor-pointer transition-opacity',
              selectedJobId && selectedJobId !== job.id && 'opacity-50'
            )}
            onClick={() => onJobClick?.(job.id)}
          >
            {/* Job name */}
            <div className="w-24 text-sm truncate text-[var(--text-secondary)]" title={job.title}>
              {job.title}
            </div>

            {/* Time bar */}
            <div className="flex-1 h-7 bg-[var(--bg-tertiary)] rounded relative overflow-hidden">
              {job.hasTimeData ? (
                <div
                  className={cn(
                    'absolute h-full rounded transition-all duration-300',
                    statusColorClasses[job.status as JobNodeStatus] || statusColorClasses.pending,
                    selectedJobId === job.id && 'ring-2 ring-indigo-500 ring-offset-1'
                  )}
                  style={{
                    left: `${job.startPercent}%`,
                    width: `${job.widthPercent}%`,
                  }}
                  title={`${job.title || 'Untitled'}: ${job.status}`}
                >
                  {/* Inner text (if width is sufficient) */}
                  {job.widthPercent > 15 && (
                    <span className="absolute inset-0 flex items-center justify-center text-xs text-white font-medium truncate px-1">
                      {job.title || 'Untitled'}
                    </span>
                  )}
                </div>
              ) : (
                // Show placeholder when no time data available
                <div className="absolute inset-0 flex items-center justify-center text-xs text-[var(--text-tertiary)]">
                  {job.status === 'pending' ? 'Waiting...' : 'No time data'}
                </div>
              )}
            </div>

            {/* Status label */}
            <div className="w-20 text-xs text-right">
              <span
                className={cn(
                  'inline-block px-2 py-0.5 rounded-full text-white',
                  (statusColorClasses[job.status as JobNodeStatus] || statusColorClasses.pending).replace('animate-pulse', '')
                )}
              >
                {job.status}
              </span>
            </div>
          </div>
        ))}
      </div>

      {/* Legend */}
      <div className="flex items-center gap-4 text-xs text-[var(--text-tertiary)] pt-2 border-t border-[var(--border-subtle)]">
        <span className="flex items-center gap-1">
          <div className="w-3 h-3 rounded bg-gray-300" /> Pending
        </span>
        <span className="flex items-center gap-1">
          <div className="w-3 h-3 rounded bg-blue-400" /> Active
        </span>
        <span className="flex items-center gap-1">
          <div className="w-3 h-3 rounded bg-[var(--color-yellow-500)]" /> Running
        </span>
        <span className="flex items-center gap-1">
          <div className="w-3 h-3 rounded bg-green-500" /> Completed
        </span>
        <span className="flex items-center gap-1">
          <div className="w-3 h-3 rounded bg-[var(--color-red-500)]" /> Failed
        </span>
      </div>
    </div>
  );
}
