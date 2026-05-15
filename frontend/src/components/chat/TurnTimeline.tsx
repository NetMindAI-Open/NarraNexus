/**
 * TurnTimeline — inline block-by-block rendering of a single agent
 * turn's events (thinking · tool_call · tool_output · reply ·
 * native_output), in the order they arrived.
 *
 * Design — order is chronological (so the user sees the agent's actual
 * rhythm "think → tool → reply"); the *styling* sorts the blocks into
 * two semantic tiers so the eye lands on what the user should read.
 *
 *   Tier ANSWER — content aimed at the user. Marked by a SOLID left rule.
 *   - reply: the authoritative answer (the agent called
 *     send_message_to_user_directly). Peak treatment — thick solid
 *     accent rule, faint accent fill, accent label, body one notch
 *     larger.
 *   - native_output: raw model text aimed at the user but not routed
 *     through the reply tool. Same tier, one notch below — solid
 *     neutral rule, full-strength body, no fill, no accent.
 *
 *   Tier PROCESS — how the agent got there, skimmable. Marked by a
 *   DASHED left rule.
 *   - thinking: internal monologue. Dashed tertiary rule, dimmest tone
 *     throughout — recedes.
 *   - tool_call / tool_output: single-line mono affordances; full
 *     args/output live in the right-side Execution panel.
 *
 * Solid-vs-dashed left rule is the at-a-glance tier signal; colour +
 * size rank within a tier. (2026-05-12 review with Xiong established
 * the chronological-blocks model; 2026-05-14 reworked the styling into
 * these tiers after thinking and native_output proved hard to tell
 * apart — the earlier "make thinking dimmer" pass was a no-op because
 * .markdown-content's explicit color/size overrode the ancestor
 * utility classes; the real hook is the markdown-* variant classes in
 * index.css.)
 *
 * Per-block expand/collapse state is keyed by event.id and lives in
 * this component's local state — fine because the parent ChatPanel
 * keeps the same TurnTimeline mounted across re-renders during a
 * single turn (clears on next user submit).
 */
import { memo, useState, useMemo } from 'react';
import { Brain, Wrench, MessageSquare, ChevronDown, ChevronRight } from 'lucide-react';
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
  return (
    <div
      className={cn(
        'message-assistant px-4 py-3',
        'border-l-4 border-dashed border-[var(--text-tertiary)]',
        'text-[var(--text-tertiary)]',
        isStreaming && 'animate-fade-in',
      )}
    >
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.16em] font-mono text-[var(--text-tertiary)] mb-2">
        <Brain className="w-3 h-3" />
        <span>Thinking</span>
      </div>
      <div className="text-sm leading-relaxed">
        {isStreaming ? (
          <div className="whitespace-pre-wrap">{content}</div>
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

  return (
    <div
      className={cn(
        'flex items-start gap-2 text-xs font-mono text-[var(--text-secondary)]',
        'px-3 py-1.5 rounded-md bg-[var(--bg-tertiary)]/40 border border-[var(--border-subtle)]',
        isStreaming && 'animate-fade-in',
      )}
    >
      <Wrench className="w-3.5 h-3.5 mt-0.5 text-[var(--accent-primary)] shrink-0" />
      <div className="min-w-0 flex-1">
        <button
          onClick={() => setExpanded((p) => !p)}
          className="flex items-center gap-1 w-full text-left"
        >
          {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
          <span className="font-semibold text-[var(--text-primary)]">{friendlyName}</span>
          {!expanded && argsPreview && (
            <span className="ml-2 truncate text-[var(--text-tertiary)]">{argsPreview}</span>
          )}
        </button>
        {expanded && (
          <pre className="mt-1.5 text-[10px] whitespace-pre-wrap break-all text-[var(--text-tertiary)]">
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
  const [expanded, setExpanded] = useState(false);
  return (
    <div
      className={cn(
        'pl-3 text-[10px] font-mono text-[var(--text-tertiary)]',
        isStreaming && 'animate-fade-in',
      )}
    >
      <button
        onClick={() => setExpanded((p) => !p)}
        className="flex items-center gap-1"
      >
        {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
        <span>output · {toolName.split('__').pop()}</span>
      </button>
      {expanded && (
        // No max-h / overflow — we deliberately want a single scroll
        // surface (the parent message list). A bounded inner box here
        // makes the user "double-scroll": once for the page, once for
        // each tool output. Long outputs just push the rest of the
        // turn down; the user can collapse the block to recover space.
        <pre className="mt-1 whitespace-pre-wrap break-all">
          {output}
        </pre>
      )}
    </div>
  );
});

const ReplyBlock = memo(function ReplyBlock({
  content,
  isStreaming,
  isFallback,
}: {
  content: string;
  isStreaming: boolean;
  isFallback: boolean;
}) {
  // Tier: ANSWER (peak). Reply is the agent's authoritative, user-facing
  // speech — the one block the user should land on first. Strongest
  // treatment in the timeline: a thick *solid* accent left rule, a faint
  // accent fill, an accent label, and a body one notch larger than the
  // default. The size bump comes from the `markdown-reply` variant class
  // (index.css) — styling the container can't enlarge Markdown content
  // because `.markdown-content` sets an explicit font-size.
  return (
    <div
      className={cn(
        'message-assistant px-4 py-3',
        'border-l-[6px] border-[var(--accent-primary)]',
        'bg-[var(--accent-primary)]/5',
        isStreaming && 'animate-fade-in',
      )}
    >
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.16em] font-mono text-[var(--accent-primary)] mb-2">
        <MessageSquare className="w-3 h-3" />
        <span>Reply</span>
        {isFallback && (
          <span className="ml-auto text-[var(--color-yellow-500)]">
            ↻ helper_llm fallback
          </span>
        )}
      </div>
      {/* Streaming path renders plain pre-wrap text (the <Markdown>
          re-parse cost per delta tanks input latency on long replies —
          2026-05-12 catch); the settled path switches to <Markdown> with
          the `markdown-reply` size variant. */}
      <div className="leading-relaxed">
        {isStreaming ? (
          <div className="whitespace-pre-wrap text-[1.05rem]">{content}</div>
        ) : (
          <Markdown content={content} className="markdown-reply" />
        )}
      </div>
    </div>
  );
});

const NativeOutputBlock = memo(function NativeOutputBlock({
  content,
  isStreaming,
}: {
  content: string;
  isStreaming: boolean;
}) {
  // Tier: ANSWER (secondary). native_output IS speech aimed at the user
  // — the model just didn't route it through send_message_to_user_-
  // directly. So it belongs in the same tier as Reply, one notch below:
  // a *solid* left rule (content, not process) and a full-strength body,
  // but in a neutral secondary tone instead of Reply's accent, and with
  // no accent fill. Previously it shared Thinking's dashed-tertiary +
  // opacity-80 treatment, which made the two indistinguishable.
  //
  // native_output never goes through <Markdown> (always plain pre-wrap),
  // so the body colour is governed directly — full strength via
  // `.message-assistant`'s text-primary, no dimming.
  return (
    <div
      className={cn(
        'message-assistant px-4 py-3',
        'border-l-4 border-[var(--text-secondary)]',
        isStreaming && 'animate-fade-in',
      )}
    >
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.16em] font-mono text-[var(--text-secondary)] mb-1.5">
        <MessageSquare className="w-3 h-3" />
        <span>Native output</span>
      </div>
      <div className="text-[0.95rem] leading-relaxed whitespace-pre-wrap">{content}</div>
    </div>
  );
});

export function TurnTimeline({ events, isStreaming = false }: TurnTimelineProps) {
  if (events.length === 0) return null;

  return (
    <div className="space-y-3">
      {events.map((event) => {
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
          case 'reply':
            return (
              <ReplyBlock
                key={event.id}
                content={event.content}
                isStreaming={isStreaming}
                isFallback={event.reply_via === 'helper_llm_fallback'}
              />
            );
          case 'native_output':
            return (
              <NativeOutputBlock
                key={event.id}
                content={event.content}
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
