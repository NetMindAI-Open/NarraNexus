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
import { useTranslation } from 'react-i18next';
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

type FallbackKind = 'none' | 'no_reply' | 'after_error';

function fallbackKindFromReplyVia(replyVia: string | undefined): FallbackKind {
  if (replyVia === 'helper_llm_after_error') return 'after_error';
  if (replyVia === 'helper_llm_no_reply' || replyVia === 'helper_llm_fallback') {
    // legacy 'helper_llm_fallback' was the pre-rename name for
    // helper_llm_no_reply; treat it the same so old persisted rows
    // still show the info badge instead of nothing.
    return 'no_reply';
  }
  return 'none';
}

const ReplyBlock = memo(function ReplyBlock({
  content,
  isStreaming,
  fallbackKind,
}: {
  content: string;
  isStreaming: boolean;
  fallbackKind: FallbackKind;
}) {
  const { t } = useTranslation();
  // Tier: ANSWER (peak). Reply is the agent's authoritative, user-facing
  // speech — the one block the user should land on first.
  // NM tier: ANSWER (peak). Reply is the agent's authoritative reply —
  // the user should land on it first. Rendered as a paper-warm bubble
  // with a Silicon-colored bracket-edge top-left. Body uses the
  // `markdown-reply` variant, sized to match the chat bubble body
  // (0.95rem) — it used to be bumped to 1.05rem but that read larger
  // than the bubbles. No accent-fill or thick left rule — those broke
  // the species-color discipline (Axiom #1 says accent can't double as
  // "this is a reply").
  return (
    <div
      className={cn(
        'relative px-4 py-3 rounded-[var(--radius-lg)]',
        isStreaming && 'animate-fade-in',
      )}
      style={{
        background: 'var(--nm-paper-warm)',
        border: '1px solid var(--nm-hairline)',
        color: 'var(--nm-ink)',
      }}
    >
      {/* Silicon bracket-edge tl — species marker for "AI reply" */}
      <span
        aria-hidden="true"
        style={{
          position: 'absolute',
          top: -1,
          left: -1,
          width: 10,
          height: 10,
          borderTop: '1.5px solid var(--color-silicon)',
          borderLeft: '1.5px solid var(--color-silicon)',
          pointerEvents: 'none',
        }}
      />
      <div
        className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.14em] mb-2"
        style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-silicon)' }}
      >
        <MessageSquare className="w-3 h-3" />
        <span>{t('chat.timeline.reply')}</span>
        {fallbackKind === 'no_reply' && (
          // Soft / informational: the agent finished thinking but didn't
          // call the reply tool; helper_llm wrote what it should have.
          // Nothing broke — surface the recovery via a muted accent.
          <span
            className="ml-auto"
            style={{ color: 'var(--color-silicon)' }}
            title={t('chat.timeline.helperFallbackTip')}
          >
            {t('chat.timeline.helperFallback')}
          </span>
        )}
        {fallbackKind === 'after_error' && (
          // Warning: a step in this turn actually failed. helper_llm
          // wrote a recovery reply from what completed. Tooltip carries
          // the operational story; raw error_type stays in logs.
          <span
            className="ml-auto"
            style={{ color: 'var(--color-warning)' }}
            title={t('chat.timeline.recoveredAfterErrorTip')}
          >
            {t('chat.timeline.recoveredAfterError')}
          </span>
        )}
      </div>
      <div className="leading-relaxed">
        {isStreaming ? (
          <div className="whitespace-pre-wrap text-[0.95rem]">{content}</div>
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
  const { t } = useTranslation();
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
  // NM tier: ANSWER (secondary). native_output is speech meant for the
  // user but not routed through send_message_to_user_directly. Same
  // visual family as Reply (paper-warm + silicon edge) but the label
  // reads [ NATIVE ] in ink-70 instead of silicon-colored.
  return (
    <div
      className={cn(
        'relative px-4 py-3 rounded-[var(--radius-lg)]',
        isStreaming && 'animate-fade-in',
      )}
      style={{
        background: 'var(--nm-paper-warm)',
        border: '1px solid var(--nm-hairline)',
        color: 'var(--nm-ink)',
      }}
    >
      <span
        aria-hidden="true"
        style={{
          position: 'absolute',
          top: -1,
          left: -1,
          width: 10,
          height: 10,
          borderTop: '1.5px solid var(--color-silicon)',
          borderLeft: '1.5px solid var(--color-silicon)',
          pointerEvents: 'none',
        }}
      />
      <div
        className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.14em] mb-1.5"
        style={{ fontFamily: 'var(--font-mono)', color: 'var(--nm-ink70)' }}
      >
        <MessageSquare className="w-3 h-3" />
        <span>{t('chat.timeline.nativeOutput')}</span>
      </div>
      <div className="text-[0.95rem] leading-relaxed whitespace-pre-wrap">{content}</div>
    </div>
  );
});

export function TurnTimeline({ events, isStreaming = false }: TurnTimelineProps) {
  if (events.length === 0) return null;

  // NM "one turn = one shared rail" rule: every sub-block (thinking /
  // tool / output / reply / native) sits under a single 1px ink-30
  // vertical line on the left, marking the whole stack as one turn.
  return (
    <div
      className="space-y-3 relative pl-3"
      style={{
        borderLeft: '1px solid var(--nm-ink30)',
      }}
    >
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
                fallbackKind={fallbackKindFromReplyVia(event.reply_via)}
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
