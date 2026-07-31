/**
 * TurnTimeline — inline block-by-block rendering of a single agent
 * turn's PROCESS events (thinking · tool_call · tool_output), in the
 * order they arrived.
 *
 * Division of labour (2026-07-30): the answer tier moved out. Replies
 * and native_output render in the bubble via SegmentedReply (cut by
 * lib/segmentTurn); the live plan renders pinned at the bottom of
 * ProcessPanel. This component keeps only the skimmable process rail —
 * rendering reply here again would print the same sentence twice.
 *
 * Blocks are chronological (so the user sees the agent's actual rhythm
 * "think → tool → think → tool"); thinking recedes (dashed rule, dim
 * tone), tools are single-line mono affordances whose full args/output
 * live in the right-side Execution panel. (2026-05-12 review with Xiong
 * established the chronological-blocks model; the markdown-* variant
 * classes in index.css are the hook that dims settled thinking —
 * .markdown-content's explicit color wins over ancestor utilities.)
 *
 * Per-block expand/collapse state is keyed by event.id and lives in
 * this component's local state — fine because the parent keeps the
 * same TurnTimeline mounted across re-renders during a single turn.
 */
import { memo, useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Brain,
  Wrench,
  ChevronDown,
  ChevronRight,
} from 'lucide-react';
import type { TurnEvent } from '@/types';
import { Markdown } from '@/components/ui';
import { cn } from '@/lib/utils';

interface TurnTimelineProps {
  events: TurnEvent[];
  /** When true, the timeline is animated as it grows (e.g. fade-in on
   *  new event). When false (e.g. completed turn still in view), the
   *  blocks render in their final, settled state. */
  isStreaming?: boolean;
}

const TOOL_ARGS_PREVIEW_CHAR_LIMIT = 80;

// All block components are wrapped in React.memo because TurnTimeline
// re-renders on every WebSocket delta during streaming. Without memo,
// a single thinking delta forces every sibling block (tool calls,
// prior thinking, reply) to reconcile too — for long turns with
// dozens of events this scaled badly enough to make the input box
// laggy on agent_5d8962… 2026-05-12. Each block now only re-renders
// when its own primitive props change; React.memo's default shallow
// equality on `content` / `output` / `isStreaming` is sufficient
// because those props are primitive strings/booleans.
const ThinkingBlock = memo(function ThinkingBlock({
  content,
  isStreaming,
}: {
  content: string;
  isStreaming: boolean;
}) {
  const { t } = useTranslation();
  // Tier: PROCESS. Thinking is the agent's internal monologue — not
  // something the user must read. It recedes: a *dashed* left rule
  // (dashed = process; solid = content-the-user-reads) and the dimmest
  // tone throughout.
  //
  // The settled body goes through <Markdown>, whose `.markdown-content`
  // sets an explicit `color` that wins over any ancestor utility class —
  // so the `text-[var(--text-tertiary)]` on the container only reaches
  // the label + the streaming plain-text path. The `markdown-dim`
  // variant class (index.css) is what actually dims the settled body.
  //
  // Streaming caveat: <Markdown> re-parses the entire content on every
  // re-render, so feeding it a new full string per delta tanks input
  // latency the longer the thinking gets (catch from Bin during the
  // 2026-05-12 deploy). While streaming we therefore render plain
  // pre-wrap text; once the turn settles (isStreaming=false, also the
  // path used by historical timelines) we switch to Markdown so
  // headings / bullets / code render properly.
  // NM tier: PROCESS — recedes into ink-50 dim. The dashed border-left
  // stays at the *row level* (drawn against the shared turn rail by the
  // outer wrapper); inside the block we paint nothing on the left.
  return (
    <div
      className={cn(
        'pl-4 py-2',
        isStreaming && 'animate-fade-in',
      )}
      style={{ color: 'var(--nm-ink50)' }}
    >
      <div
        className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.16em] mb-2"
        style={{ fontFamily: 'var(--font-mono)', color: 'var(--nm-ink50)' }}
      >
        <Brain className="w-3 h-3" />
        <span>{t('chat.timeline.thinking')}</span>
      </div>
      <div className="text-sm leading-relaxed">
        {isStreaming ? (
          <div className="whitespace-pre-wrap" style={{ color: 'var(--nm-ink50)' }}>{content}</div>
        ) : (
          <Markdown content={content} className="markdown-dim" />
        )}
      </div>
    </div>
  );
});

const ToolCallBlock = memo(function ToolCallBlock({
  toolName,
  toolInput,
  isStreaming,
}: {
  toolName: string;
  toolInput: Record<string, unknown>;
  isStreaming: boolean;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  // Display the tool name without the long MCP namespace prefix —
  // "mcp__chat_module__get_chat_history" → "get_chat_history".
  const friendlyName = useMemo(() => {
    const parts = toolName.split('__');
    return parts[parts.length - 1] || toolName;
  }, [toolName]);

  // One-line argument preview — first 80 chars of JSON-stringified
  // input (truncated). Full args available on expand.
  const argsJson = useMemo(() => {
    try {
      return JSON.stringify(toolInput);
    } catch {
      return '';
    }
  }, [toolInput]);
  const argsPreview = argsJson.length > TOOL_ARGS_PREVIEW_CHAR_LIMIT
    ? argsJson.slice(0, TOOL_ARGS_PREVIEW_CHAR_LIMIT) + '…'
    : argsJson;

  // NM: bracket-tagged [ tool ] label + mono args; SunkenWell-feeling
  // body via paper-warm bg + hairline + radius-sm.
  return (
    <div
      className={cn(
        'flex items-start gap-2 text-xs px-3 py-1.5 rounded-[var(--radius-sm)]',
        isStreaming && 'animate-fade-in',
      )}
      style={{
        background: 'var(--nm-paper-warm)',
        border: '1px solid var(--nm-hairline)',
        color: 'var(--nm-ink70)',
        fontFamily: 'var(--font-mono)',
      }}
    >
      <Wrench className="w-3.5 h-3.5 mt-0.5 shrink-0" style={{ color: 'var(--nm-ink50)' }} />
      <div className="min-w-0 flex-1">
        <button
          onClick={() => setExpanded((p) => !p)}
          className="flex items-center gap-1.5 w-full text-left"
        >
          {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
          <span
            className="text-[10px] uppercase tracking-[0.12em] shrink-0"
            style={{ color: 'var(--nm-ink30)' }}
          >
            {t('chat.timeline.toolLabel')}
          </span>
          <span className="font-semibold" style={{ color: 'var(--nm-ink)' }}>
            {friendlyName}
          </span>
          {!expanded && argsPreview && (
            <span className="ml-2 truncate" style={{ color: 'var(--nm-ink50)' }}>
              {argsPreview}
            </span>
          )}
        </button>
        {expanded && (
          <pre
            className="mt-1.5 text-[10px] whitespace-pre-wrap break-all"
            style={{ color: 'var(--nm-ink50)' }}
          >
            {argsJson}
          </pre>
        )}
      </div>
    </div>
  );
});

const ToolOutputBlock = memo(function ToolOutputBlock({
  toolName,
  output,
  isStreaming,
}: {
  toolName: string;
  output: string;
  isStreaming: boolean;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  return (
    <div
      className={cn(
        'pl-4 text-[10px]',
        isStreaming && 'animate-fade-in',
      )}
      style={{ color: 'var(--nm-ink50)', fontFamily: 'var(--font-mono)' }}
    >
      <button
        onClick={() => setExpanded((p) => !p)}
        className="flex items-center gap-1.5"
      >
        {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
        <span style={{ color: 'var(--nm-ink30)' }} className="uppercase tracking-[0.12em]">
          {t('chat.timeline.outputLabel')}
        </span>
        <span>{toolName.split('__').pop()}</span>
      </button>
      {expanded && (
        // No max-h / overflow — single parent scroll surface only.
        <pre
          className="mt-1 whitespace-pre-wrap break-all"
          style={{ color: 'var(--nm-ink70)' }}
        >
          {output}
        </pre>
      )}
    </div>
  );
});

export function TurnTimeline({ events, isStreaming = false }: TurnTimelineProps) {
  // Division of labour: process belongs to the timeline, answers to the
  // bubble (SegmentedReply, cut by segmentTurn). The answer tier is
  // filtered out here — keeping it would print the same sentence in both
  // the bubble and the collapsed region. Plans don't render here either:
  // they live in ProcessPanel's pinned footer.
  const processEvents = useMemo(
    () => events.filter(
      (e) => e.type === 'thinking' || e.type === 'tool_call' || e.type === 'tool_output',
    ),
    [events],
  );
  if (processEvents.length === 0) return null;

  // NM "one turn = one shared rail" rule: every process block (thinking /
  // tool / output) sits under a single 1px ink-30 vertical line on the
  // left, marking the whole stack as one turn.
  return (
    <div
      className="space-y-3 relative pl-3"
      style={{
        borderLeft: '1px solid var(--nm-ink30)',
      }}
    >
      {processEvents.map((event) => {
        switch (event.type) {
          case 'thinking':
            return (
              <ThinkingBlock
                key={event.id}
                content={event.content}
                isStreaming={isStreaming}
              />
            );
          case 'tool_call':
            return (
              <ToolCallBlock
                key={event.id}
                toolName={event.tool_name}
                toolInput={event.tool_input}
                isStreaming={isStreaming}
              />
            );
          case 'tool_output':
            return (
              <ToolOutputBlock
                key={event.id}
                toolName={event.tool_name}
                output={event.output}
                isStreaming={isStreaming}
              />
            );
          default:
            return null;
        }
      })}
    </div>
  );
}
