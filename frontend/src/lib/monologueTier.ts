/**
 * monologueTier — is this thinking frame NexusPower's own narration?
 *
 * Under the monologue/expression contract the framework's plain text is
 * private deliberation and the reply lives in an expression tool's argument.
 * That plain text still streams to the UI, on the SAME channel as provider
 * chain-of-thought — both arrive as `agent_thinking`. They are not the same
 * tier: narration is what the agent is doing right now and reads as prose;
 * CoT is scratchpad. Design A′ renders the first at the "progress" tier and
 * lets the second keep receding.
 *
 * Why equality and not `!!monologue`
 * ----------------------------------
 * A frame whose `monologue` is only PART of `thinking_content` cannot be
 * split here: the union and the subset are both present, the positions are
 * not. The batcher stopped producing such frames on 2026-08-30 (it flushes on
 * a tier switch), so equality and truthiness give the same answer today — the
 * test stays because it points the failure the safe way if mixing ever comes
 * back. A mixed frame reports false: the narration hides among CoT, which
 * loses nothing. `!!monologue` would instead promote provider scratchpad to
 * the progress tier — showing the user something we promised was scratchpad.
 *
 * Same rule on both paths — live frames (chatStore) and replayed rows
 * (timelineToEvents, where the backend already collapsed it to a bool) — so
 * the live view and the post-refresh view agree, which is the invariant
 * `segmentTurn` is built on.
 *
 * That agreement covers all three replay paths since 2026-08-30: the
 * event-log reload of an ended turn, the WS reconnect of a run still going,
 * and the team roster's observation socket. `run_recorder` tags each
 * (tier-pure) segment, `broadcaster` carries the in-flight one, and
 * `wsManager.translateReconnectFrame` hands both back in this shape.
 */

/**
 * True when the whole frame is monologue. `content` is the frame's full
 * display text; `monologue` the subset the backend tagged.
 */
export function isMonologueFrame(content: string, monologue?: string): boolean {
  return !!monologue && monologue === content;
}
