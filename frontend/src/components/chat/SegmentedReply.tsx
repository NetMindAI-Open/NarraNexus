/**
 * SegmentedReply — render a turn's Segment[] as the times the agent
 * actually spoke.
 *
 * A turn may speak several times (m of its n tool calls are replies to
 * the user). The backend still stores one record per turn; this renders
 * that one record as m bubbles, each carrying the process that led to
 * it.
 *
 * One component serves live and history alike. `showProcess` is a prop, not
 * a mode: ChatPanel passes it for both the in-flight turn and the settled one,
 * so the live turn already renders the document it will keep. It stays a prop
 * because callers that only want the answers (a preview, a digest) still need
 * that option — the chat itself no longer uses it.
 *
 * There is ONE shape (2026-08-30, second pass): the process renders
 * inline as a document — narration, tool lines, collapsed reasoning — and the
 * reply is body prose under it. The "Reasoning & tools" drawer is gone: it hid
 * the narration this branch exists to surface, and keeping it for some turns
 * meant two transcript shapes in one product. The narration display preference
 * now does only what its name says — tone, not layout (see useNarrationTier).
 *
 * How segments are cut is lib/segmentTurn's job — this only draws.
 */
import { memo } from 'react';
import { useTranslation } from 'react-i18next';
import type { Segment } from '@/types';
import { Markdown } from '@/components/ui';
import { stripAgentDraft } from '@/lib/builderProtocol';
import { TurnTimeline } from './TurnTimeline';

export interface SegmentedReplyProps {
  segments: Segment[];
  /** Whether the collapsed process details are available (true for history bubbles, false live). */
  showProcess?: boolean;
  /**
   * Start with every process region expanded. Used when the user already
   * clicked once to get here (the "View reasoning" fetch on a historical
   * bubble) — landing on another collapsed toggle would make it two
   * clicks to see anything. Regions can still be collapsed by hand.
   */
  /** Live: the last segment is still growing — give it a streaming cursor. */
  isStreaming?: boolean;
}

type FallbackKind = 'none' | 'no_reply' | 'after_error';

// Formerly TurnTimeline's ReplyBlock badge logic; it moved here with the
// answer tier (2026-07-30). Legacy 'helper_llm_fallback' is the
// pre-rename (2026-05-25) persisted value of helper_llm_no_reply —
// treated the same so old rows still show the recovery badge instead of
// nothing.
function fallbackKindFromReplyVia(via: string | undefined): FallbackKind {
  if (via === 'helper_llm_after_error') return 'after_error';
  if (via === 'helper_llm_no_reply' || via === 'helper_llm_fallback') return 'no_reply';
  return 'none';
}

export const SegmentedReply = memo(function SegmentedReply({
  segments,
  showProcess = false,
  isStreaming = false,
}: SegmentedReplyProps) {
  const { t } = useTranslation();

  return (
    <div className="space-y-4">
      {segments.map((segment, index) => {
        const isLast = index === segments.length - 1;
        return (
          <div key={index} className="space-y-2">
            {showProcess && segment.process.length > 0 && (
              // The process IS the transcript — narration, tool lines and
              // collapsed reasoning, in the order they happened. No outer
              // drawer: the whole point is that the run reads without
              // clicking anything. TurnTimeline decides each block's tier.
              <div data-testid={`segment-details-${index}`}>
                <TurnTimeline events={segment.process} />
              </div>
            )}

            {segment.reply && (
              <div
                data-testid={`segment-reply-${index}`}
                className="markdown-content markdown-reply text-[var(--text-primary)]"
              >
                {fallbackKindFromReplyVia(segment.reply.via) === 'no_reply' && (
                  // Soft / informational: the agent finished thinking but didn't
                  // call the reply tool; helper_llm wrote what it should have.
                  <div
                    className="text-[10px] uppercase tracking-[0.14em] mb-1"
                    style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-silicon)' }}
                    title={t('chat.timeline.helperFallbackTip')}
                  >
                    {t('chat.timeline.helperFallback')}
                  </div>
                )}
                {fallbackKindFromReplyVia(segment.reply.via) === 'after_error' && (
                  // Warning: a step in this turn actually failed. helper_llm
                  // wrote a recovery reply from what completed.
                  <div
                    className="text-[10px] uppercase tracking-[0.14em] mb-1"
                    style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-warning)' }}
                    title={t('chat.timeline.recoveredAfterErrorTip')}
                  >
                    {t('chat.timeline.recoveredAfterError')}
                  </div>
                )}
                {/* While the segment streams, render plain pre-wrap text:
                    re-parsing the whole markdown per delta saturates the
                    main thread and the UI visibly stalls, then the finished
                    reply pops in at once (the same catch ThinkingBlock and
                    the old ReplyBlock documented on 2026-05-12). Markdown
                    renders on settle. */}
                {isStreaming && (segment.reply.streaming || isLast) ? (
                  // Same metrics as the settled markdown body, so the text
                  // does not reflow when the turn lands.
                  <div className="whitespace-pre-wrap text-[0.95rem] leading-[1.75]">
                    {/* Creation studio: the <agent_draft> block's OPEN tag
                        arrives many frames before its close tag, so this
                        streaming branch is exactly where raw JSON would
                        scroll past the reader. stripAgentDraft kills the
                        unterminated form too. */}
                    {stripAgentDraft(segment.reply.content)}
                  </div>
                ) : (
                  <Markdown content={stripAgentDraft(segment.reply.content)} />
                )}
                {isStreaming && isLast && (
                  <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-[var(--accent-primary)] align-text-bottom" />
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
});
