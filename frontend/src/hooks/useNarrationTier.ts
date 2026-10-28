/**
 * useNarrationTier — "should NexusPower's narration render at the progress
 * tier right now?"
 *
 * One reader for the `interimNarration` display preference. It exists because
 * two independent surfaces need the same answer, and two copies of
 * `useUIStore((s) => s.interimNarration)` are two places to miss when the
 * default or the source of the preference changes:
 *
 *   - TurnTimeline — the single process renderer, for the live turn, the
 *     settled turn and team observation alike.
 *   - InnerThoughtCard — the Inner Thoughts tab. Easy to forget and the one
 *     that matters most: an activity row is written only when a turn sent no
 *     user-facing reply (background job, channel trigger), so those turns are
 *     narration end to end and this card is their only view.
 *
 * Resolve it at the TOP-LEVEL component and pass the answer down as a prop
 * (TurnTimeline → ThinkingBlock's `narration`, InnerThoughtCard → EntryRow's
 * `showNarration`). A shared child that reaches into the store itself gains a
 * hidden input that is not on its props, which is how you get "I passed the
 * right events and it still looks wrong" the next time it is reused.
 *
 * TurnTimeline reading this hook directly is correct — it is a top-level
 * renderer, not a shared row. Taking the preference as a prop instead would
 * force TURNTIMELINE'S OWN call sites (MessageBubble, SegmentedReply,
 * TeamMemberPanel — not callers of this hook) to thread a prop that has
 * nothing to do with them.
 */
import { useUIStore } from '@/stores/uiStore';

export function useNarrationTier(): boolean {
  return useUIStore((s) => s.interimNarration);
}
