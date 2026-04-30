/**
 * JobExpandedDetail - 列表视图中 Job 展开后的详细信息面板
 *
 * 将原本内联在 JobsPanel 中的展开区域提取为独立组件，
 * 展示 Job 的完整字段信息（IDs、配置、Payload、时间、依赖、日志、上下文、错误）。
 */

import { useState, useCallback } from 'react';
import {
  Ban,
  Loader2,
  Users,
  FileText,
  Copy,
  Check,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import { Button, Badge, ScrollArea } from '@/components/ui';
import { formatRelativeTime } from '@/lib/utils';
import type { Job } from '@/types/api';

interface JobExpandedDetailProps {
  job: Job;
  /** 当前是否正在取消 */
  isCancelling: boolean;
  /** 该状态是否允许取消 */
  canCancel: boolean;
  /** 取消 Job 回调 */
  onCancel: (e: React.MouseEvent, jobId: string) => void;
}

/** 点击复制文本，显示短暂的勾号反馈 */
function CopyableId({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [value]);

  return (
    <button
      type="button"
      onClick={handleCopy}
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-[var(--bg-tertiary)] hover:bg-[var(--accent-glow)] transition-colors cursor-pointer group/copy"
      title="Click to copy"
    >
      <span className="font-mono text-[10px] text-[var(--text-secondary)] truncate max-w-[180px]">
        {value}
      </span>
      {copied ? (
        <Check className="w-3 h-3 text-[var(--color-success)] shrink-0" />
      ) : (
        <Copy className="w-3 h-3 text-[var(--text-tertiary)] opacity-0 group-hover/copy:opacity-100 transition-opacity shrink-0" />
      )}
    </button>
  );
}

/** 区块标题 */
function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[9px] text-[var(--text-tertiary)] font-medium uppercase tracking-wider mb-1.5">
      {children}
    </div>
  );
}

export function JobExpandedDetail({
  job,
  isCancelling,
  canCancel,
  onCancel,
}: JobExpandedDetailProps) {
  const [payloadExpanded, setPayloadExpanded] = useState(false);

  // 解析 trigger_config 中可能存在的额外字段
  const triggerConfig = job.trigger_config;
  const runAt = triggerConfig?.run_at as string | undefined;
  const endCondition = triggerConfig?.end_condition as string | undefined;
  const maxIterations = triggerConfig?.max_iterations as number | undefined;

  // Payload 文本（可能很长）
  const payloadText = job.payload || '';
  const payloadLines = payloadText.split('\n');
  const isPayloadLong = payloadLines.length > 3 || payloadText.length > 300;
  const payloadPreview = isPayloadLong && !payloadExpanded
    ? payloadLines.slice(0, 3).join('\n') + (payloadLines.length > 3 ? '\n...' : '')
    : payloadText;

  return (
    <div
      className="mt-4 space-y-3 text-xs animate-fade-in"
      onClick={(e) => e.stopPropagation()}
    >
      {/* 1. IDs & Metadata */}
      <div className="p-3 bg-[var(--bg-sunken)] rounded-lg border border-[var(--border-subtle)]">
        <SectionLabel>IDs</SectionLabel>
        <div className="space-y-1.5">
          <div className="flex items-center gap-2">
            <span className="text-[var(--text-tertiary)] text-[10px] w-16 shrink-0">Job ID</span>
            <CopyableId value={job.job_id} />
          </div>
          {job.instance_id && (
            <div className="flex items-center gap-2">
              <span className="text-[var(--text-tertiary)] text-[10px] w-16 shrink-0">Instance</span>
              <CopyableId value={job.instance_id} />
            </div>
          )}
        </div>
      </div>

      {/* 2. Configuration */}
      <div className="p-3 bg-[var(--bg-sunken)] rounded-lg border border-[var(--border-subtle)]">
        <SectionLabel>Configuration</SectionLabel>
        <div className="grid grid-cols-2 gap-2 font-mono text-[10px]">
          <div>
            <span className="text-[var(--text-tertiary)]">Type: </span>
            <span className="text-[var(--accent-primary)]">{job.job_type}</span>
          </div>
          {triggerConfig?.trigger_type && (
            <div>
              <span className="text-[var(--text-tertiary)]">Trigger: </span>
              <span className="text-[var(--accent-secondary)]">{triggerConfig.trigger_type}</span>
            </div>
          )}
          {triggerConfig?.cron_expression && (
            <div>
              <span className="text-[var(--text-tertiary)]">Cron: </span>
              <span className="text-[var(--text-secondary)]">{triggerConfig.cron_expression as string}</span>
            </div>
          )}
          {triggerConfig?.interval_seconds && (
            <div>
              <span className="text-[var(--text-tertiary)]">Interval: </span>
              <span className="text-[var(--text-secondary)]">{triggerConfig.interval_seconds}s</span>
            </div>
          )}
          {runAt && (
            <div className="col-span-2">
              <span className="text-[var(--text-tertiary)]">Run at: </span>
              <span className="text-[var(--text-secondary)]">{formatRelativeTime(runAt)}</span>
            </div>
          )}
          {endCondition && (
            <div className="col-span-2">
              <span className="text-[var(--text-tertiary)]">End condition: </span>
              <span className="text-[var(--text-secondary)]">{endCondition}</span>
            </div>
          )}
          {maxIterations != null && (
            <div>
              <span className="text-[var(--text-tertiary)]">Max iter: </span>
              <span className="text-[var(--text-secondary)]">{maxIterations}</span>
            </div>
          )}
        </div>
      </div>

      {/* 3. Payload */}
      {payloadText && (
        <div className="p-3 bg-[var(--bg-sunken)] rounded-lg border border-[var(--border-subtle)]">
          <SectionLabel>Payload</SectionLabel>
          <pre className="text-[10px] font-mono text-[var(--text-secondary)] whitespace-pre-wrap break-words leading-relaxed">
            {payloadPreview}
          </pre>
          {isPayloadLong && (
            <button
              type="button"
              onClick={() => setPayloadExpanded(!payloadExpanded)}
              className="mt-1.5 flex items-center gap-1 text-[9px] text-[var(--accent-primary)] hover:text-[var(--text-primary)] transition-colors"
            >
              {payloadExpanded ? (
                <>
                  <ChevronUp className="w-3 h-3" />
                  Show less
                </>
              ) : (
                <>
                  <ChevronDown className="w-3 h-3" />
                  Show more
                </>
              )}
            </button>
          )}
        </div>
      )}

      {/* 4. Timing */}
      <div className="p-3 bg-[var(--bg-sunken)] rounded-lg border border-[var(--border-subtle)]">
        <SectionLabel>Timing</SectionLabel>
        <div className="grid grid-cols-2 gap-2 font-mono text-[10px]">
          {job.created_at && (
            <div>
              <span className="text-[var(--text-tertiary)]">Created: </span>
              <span className="text-[var(--text-secondary)]">{formatRelativeTime(job.created_at)}</span>
            </div>
          )}
          {job.updated_at && (
            <div>
              <span className="text-[var(--text-tertiary)]">Updated: </span>
              <span className="text-[var(--text-secondary)]">{formatRelativeTime(job.updated_at)}</span>
            </div>
          )}
          {job.last_run_at && (
            <div>
              <span className="text-[var(--text-tertiary)]">Last run: </span>
              <span className="text-[var(--text-secondary)]">
                {job.last_run_at}{job.last_run_timezone ? ` (${job.last_run_timezone})` : ''}
              </span>
            </div>
          )}
          {job.next_run_at && (
            <div>
              <span className="text-[var(--text-tertiary)]">Next run: </span>
              <span className="text-[var(--color-success)]">
                {job.next_run_at}{job.next_run_timezone ? ` (${job.next_run_timezone})` : ''}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* 5. Dependencies */}
      {job.depends_on && job.depends_on.length > 0 && (
        <div className="p-3 bg-[var(--bg-sunken)] rounded-lg border border-[var(--border-subtle)]">
          <SectionLabel>Dependencies</SectionLabel>
          <div className="flex flex-wrap gap-1.5">
            {job.depends_on.map((dep) => (
              <Badge key={dep} variant="outline" size="sm" className="font-mono text-[9px]">
                {dep}
              </Badge>
            ))}
          </div>
        </div>
      )}

      {/* 6. Process Log */}
      {job.process && job.process.length > 0 && (
        <div className="p-3 bg-[var(--bg-sunken)] rounded-lg border border-[var(--border-subtle)]">
          <SectionLabel>Process Log ({job.process.length} entries)</SectionLabel>
          <ScrollArea className="max-h-32">
            <div className="space-y-0.5">
            {job.process.map((entry, idx) => (
              <div
                key={idx}
                className="font-mono text-[10px] text-[var(--text-secondary)] px-1.5 py-0.5 rounded hover:bg-[var(--bg-tertiary)] transition-colors"
              >
                <span className="text-[var(--text-tertiary)] mr-1.5 select-none">{idx + 1}.</span>
                {entry}
              </div>
            ))}
            </div>
          </ScrollArea>
        </div>
      )}

      {/* 7. Context — Target User & Linked Narrative */}
      {job.related_entity_id && (
        <div className="p-3 bg-[var(--accent-primary)]/5 rounded-lg border border-[var(--accent-primary)]/20">
          <div className="text-[9px] text-[var(--accent-primary)] font-medium uppercase tracking-wider flex items-center gap-1.5 mb-2">
            <Users className="w-3 h-3" />
            Target User (Execution Identity)
          </div>
          <span
            className="inline-flex items-center px-2 py-1 text-[9px] rounded-lg bg-[var(--accent-primary)]/10 text-[var(--accent-primary)] border border-[var(--accent-primary)]/20 font-mono"
            title={job.related_entity_id}
          >
            {job.related_entity_id.length > 20 ? `${job.related_entity_id.slice(0, 20)}...` : job.related_entity_id}
          </span>
        </div>
      )}

      {job.narrative_id && (
        <div className="p-3 bg-[var(--accent-secondary)]/5 rounded-lg border border-[var(--accent-secondary)]/20">
          <div className="text-[9px] text-[var(--accent-secondary)] font-medium uppercase tracking-wider flex items-center gap-1.5 mb-2">
            <FileText className="w-3 h-3" />
            Linked Narrative
          </div>
          <span className="text-[10px] font-mono text-[var(--text-secondary)]">
            {job.narrative_id}
          </span>
        </div>
      )}

      {/* 8. Error */}
      {job.last_error && (
        <div className="p-3 bg-[var(--color-error)]/10 rounded-lg border border-[var(--color-error)]/20">
          <SectionLabel>
            <span className="text-[var(--color-error)]">Error</span>
          </SectionLabel>
          <p className="text-[10px] font-mono text-[var(--text-secondary)] whitespace-pre-wrap break-words">
            {job.last_error}
          </p>
        </div>
      )}

      {/* 9. Cancel Button */}
      {canCancel && (
        <div className="pt-3 border-t border-[var(--border-subtle)]">
          <Button
            variant="ghost"
            size="sm"
            onClick={(e) => onCancel(e, job.job_id)}
            disabled={isCancelling}
            className="text-[var(--color-error)] hover:bg-[var(--color-error)]/10 hover:shadow-[0_0_10px_var(--color-error)/20]"
          >
            {isCancelling ? (
              <>
                <Loader2 className="w-3 h-3 mr-1.5 animate-spin" />
                Cancelling...
              </>
            ) : (
              <>
                <Ban className="w-3 h-3 mr-1.5" />
                Cancel Job
              </>
            )}
          </Button>
        </div>
      )}
    </div>
  );
}

export default JobExpandedDetail;
