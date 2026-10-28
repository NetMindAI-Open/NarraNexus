/**
 * @file_name: scrollStickiness.ts
 * @author: NarraNexus
 * @date: 2026-08-13
 * @description: Whether a growing transcript may take the scroll position.
 *
 * The team room called `scrollIntoView` unconditionally whenever the message
 * count changed. In a room where six agents can answer at once, a user scrolled
 * up reading something from two minutes ago is yanked back to the bottom every
 * few seconds — the transcript is least readable exactly when it is busiest.
 *
 * The private chat has had the rule for a long time: follow along only while
 * the viewport is already near the bottom. It lived inline in a JSX scroll
 * handler, so the team room could not use it without copying it. This branch
 * has already found two copies of a hash palette that had silently diverged;
 * extracting is cheaper than discovering the same thing here later.
 *
 * The thresholds are the private chat's, unchanged. They are not derived from
 * anything — they are the numbers the product already behaves by, and moving
 * them would make the two surfaces feel different for no stated reason.
 */

/** Slack for "the reader is at the bottom": one short line of drift. */
const BOTTOM_SLACK_PX = 100;

/** Slack for "the reader has reached the top", where older history loads. */
const TOP_SLACK_PX = 50;

interface ScrollLike {
  scrollTop: number;
  clientHeight: number;
  scrollHeight: number;
}

/**
 * May new content scroll the viewport?
 *
 * A missing element counts as YES: before the first paint there is nothing to
 * measure, and defaulting to "do not follow" would open the room scrolled to
 * the top of its history.
 */
export function isNearBottom(el: ScrollLike | null | undefined): boolean {
  if (!el) return true;
  return el.scrollHeight - el.scrollTop - el.clientHeight < BOTTOM_SLACK_PX;
}

/**
 * Has the reader reached the top, where the next page of history belongs?
 *
 * A missing element counts as NO — the opposite default, deliberately: this
 * predicate triggers a fetch, and defaulting to true would fire a history load
 * before the transcript has rendered anything.
 */
export function isNearTop(el: ScrollLike | null | undefined): boolean {
  if (!el) return false;
  return el.scrollTop < TOP_SLACK_PX;
}
