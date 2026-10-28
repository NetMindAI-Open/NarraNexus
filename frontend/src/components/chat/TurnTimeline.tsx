/**
 * TurnTimeline — inline block-by-block rendering of a single agent
 * turn's PROCESS events (thinking · tool_call · tool_output), in the
 * order they arrived.
 *
 * Division of labour (2026-07-30): the answer tier moved out. Replies
 * and native_output render in the bubble via SegmentedReply (cut by
 * lib/segmentTurn); the live plan renders in the pinned PlanStrip above
 * the composer. This component keeps only the skimmable process rail —
 * rendering reply here again would print the same sentence twice.
 *
 * Blocks are chronological (so the user sees the agent's actual rhythm
 * "think → tool → think → tool"); thinking recedes (dashed rule, dim
 * tone) — except NexusPower's own narration, which since 2026-08-30
 * renders one rung brighter as the PROGRESS tier (design A′; still a
 * process block, never a bubble) — and tools are single-line mono
 * affordances whose full args/output
 * live in the right-side Execution panel. (2026-05-12 review with Xiong
 * established the chronological-blocks model; the markdown-* variant
 * classes in index.css are the hook that dims settled thinking —
 * .markdown-content's explicit color wins over ancestor utilities.)
 *
 * Per-block expand/collapse state is keyed by event.id and lives in
 * this component's local state — fine because the parent keeps the
 * same TurnTimeline mounted across re-renders during a single turn.
 */
import { Suspense, memo, useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Brain,
  Wrench,
  ChevronDown,
  ChevronRight,
  Milestone,
} from 'lucide-react';
import type { TurnEvent } from '@/types';
import { Markdown } from '@/components/ui';
import { cn } from '@/lib/utils';
import { useNarrationTier } from '@/hooks/useNarrationTier';
import { TIMELINE_EVENTS, TOOL_RENDERERS, toolRendererFor, useRegistryEntries } from '@/platform/registries';
import { PluginBoundary } from '@/platform/PluginBoundary';

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
  narration,
}: {
  content: string;
  isStreaming: boolean;
  /** Tier: PROGRESS instead of the receded reasoning tone. True only for
   *  NexusPower narration AND only while the user preference is on — the
   *  caller resolves both, so this stays a pure tier switch. */
  narration?: boolean;
}) {
  const { t } = useTranslation();
  // Reasoning ALWAYS starts folded — including behind a disclosure the reader
  // just opened (Owner's call, 2026-08-31). Opening a turn's process asks for
  // its conclusions; the provider's scratch paper is bulkier than everything
  // else combined, so auto-expanding it buries the narration the reader came
  // for. Folded is not discarded: the toggle is right there (iron rule #16).
  const [open, setOpen] = useState(false);

  // PROGRESS tier — the sentence the agent writes before each tool call.
  // It is what the user reads while waiting, so it is never behind a toggle
  // and sits at near-body weight (text-secondary, one rung under the
  // answer). Its only chrome is a 12px marker in the SAME colour as the
  // text (design_system §5: an icon never gets its own grey) — enough to
  // tell it from the final answer on a reloaded turn without making it a
  // bubble. Not a bubble on purpose: promoting it out of the process
  // register is the one thing the constitution still does not allow —
  // plain text is visible, never delivered.
  if (narration) {
    return (
      <div
        className={cn('flex gap-2 pl-4 py-1.5', isStreaming && 'animate-fade-in')}
        style={{ color: 'var(--text-secondary)' }}
      >
        <Milestone className="w-3 h-3 mt-1 shrink-0" />
        <div className="text-sm leading-relaxed min-w-0">
          {isStreaming ? (
            <div className="whitespace-pre-wrap">{content}</div>
          ) : (
            <Markdown content={content} className="markdown-progress" />
          )}
        </div>
      </div>
    );
  }

  // REASONING tier — provider chain-of-thought is the agent's scratchpad,
  // not something the user must read, so it collapses to one line and opens
  // on demand. Until 2026-08-30 the WHOLE turn hid behind one drawer; now
  // the turn is open and only this recedes. Nothing became unreachable —
  // the full text is one click away (iron rule #16 is about content, not
  // about how many pixels it occupies by default).
  return (
    <div className={cn('pl-4 py-1', isStreaming && 'animate-fade-in')}>
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
        className="flex items-center gap-1.5 text-xs transition-opacity hover:opacity-80"
        style={{ fontFamily: 'var(--font-mono)', color: 'var(--nm-ink50)' }}
      >
        <ChevronRight className={cn('w-3 h-3 transition-transform', open && 'rotate-90')} />
        <Brain className="w-3 h-3" />
        <span>{t('chat.timeline.thought')}</span>
      </button>
      {open && (
        <div className="mt-1.5 text-sm leading-relaxed" style={{ color: 'var(--nm-ink50)' }}>
          {isStreaming ? (
            <div className="whitespace-pre-wrap">{content}</div>
          ) : (
            <Markdown content={content} className="markdown-dim" />
          )}
        </div>
      )}
    </div>
  );
});

const ToolCallBlock = memo(function ToolCallBlock({
  toolName,
  toolInput,
  isStreaming,
  testId,
  pending,
}: {
  toolName: string;
  toolInput: Record<string, unknown>;
  isStreaming: boolean;
  /** Stable hook for asserting the flow's shape — the retired
   *  ProcessEventRows carried one and its tests depended on it. */
  testId?: string;
  /** Name known, arguments still streaming. */
  pending?: boolean;
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
      data-testid={testId}
      data-pending={pending ? 'true' : 'false'}
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
          {/* Same rule as the output row: no name recovered → label alone. */}
          {friendlyName && (
            <span className="font-semibold" style={{ color: 'var(--nm-ink)' }}>
              {friendlyName}
            </span>
          )}
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
        {/* No name recovered → label alone; a literal placeholder word next
            to every output row reads as a bug, not as information. */}
        {toolName && <span>{toolName.split('__').pop()}</span>}
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

export function TurnTimeline({
  events,
  isStreaming = false,
}: TurnTimelineProps) {
  // Division of labour: process belongs to the timeline, answers to the
  // bubble (SegmentedReply, cut by segmentTurn). The answer tier is
  // filtered out here — keeping it would print the same sentence in both
  // the bubble and the collapsed region. Plans don't render here either:
  // they live in the pinned PlanStrip above the composer.
  // Plugin-owned event types (ui.timelineEvents) render in the same rail;
  // subscribing keeps a late registration from being filtered out.
  const pluginEvents = useRegistryEntries(TIMELINE_EVENTS);
  // A tool's output row may be owned by the plugin that ships the tool
  // (ui.toolRenderers); unowned, declined or crashing → the generic row.
  const toolRenderers = useRegistryEntries(TOOL_RENDERERS);
  const processEvents = useMemo(
    () => events.filter(
      (e) => e.type === 'thinking' || e.type === 'tool_call' || e.type === 'tool_output' || pluginEvents.some((p) => p.id === (e as { type: string }).type),
    ),
    [events, pluginEvents],
  );
  // Display preference (default on). Off restores the pre-A′ look: the same
  // blocks, same text, same order — only the tone goes back to receded.
  const showNarration = useNarrationTier();
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
                narration={showNarration && !!event.monologue}
              />
            );
          case 'tool_call':
            return (
              <ToolCallBlock
                key={event.id}
                toolName={event.tool_name}
                toolInput={event.tool_input}
                isStreaming={isStreaming}
                testId={`tool-row-${event.id}`}
                pending={event.pending}
              />
            );
          case 'tool_output': {
            const generic = () => (
              <ToolOutputBlock
                key={event.id}
                toolName={event.tool_name}
                output={event.output}
                isStreaming={isStreaming}
              />
            );
            const owner = toolRendererFor(toolRenderers, event.tool_name, event.output);
            if (!owner) return generic();
            const Custom = owner.value.component;
            return (
              <PluginBoundary key={event.id} owner={owner.owner} fallback={generic}>
                {/* A lazily-loaded renderer shows the generic row until its chunk arrives. */}
                <Suspense fallback={generic()}>
                  <Custom toolName={event.tool_name} output={event.output} isStreaming={isStreaming} />
                </Suspense>
              </PluginBoundary>
            );
          }
          default: {
            const plugin = pluginEvents.find((p) => p.id === (event as { type: string }).type);
            if (!plugin) return null;
            const Custom = plugin.value.component;
            return (
              <PluginBoundary key={event.id} owner={plugin.owner}>
                <Custom event={event as unknown as { id: string; type: string }} isStreaming={isStreaming} />
              </PluginBoundary>
            );
          }
        }
      })}
    </div>
  );
}
