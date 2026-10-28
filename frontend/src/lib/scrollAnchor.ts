/**
 * @file_name: scrollAnchor.ts
 * @author: NarraNexus
 * @date: 2026-08-19
 * @description: Keep the user's place when content is prepended to a
 * scroll container (chat history pagination).
 *
 * Anchor on the topmost currently-rendered item, not on scrollHeight
 * arithmetic: height deltas double-count anything else that changes around
 * the prepend (a loading row appearing/disappearing, images sizing in) and
 * drop the user at the top of the freshly loaded chunk. The anchor element
 * survives the prepend (stable React key), so the exact number of pixels it
 * moved IS the correction — whatever caused the movement. The height delta
 * survives only as the fallback for "there was nothing rendered yet".
 */

interface AnchorElement {
  getBoundingClientRect(): { top: number };
  readonly isConnected: boolean;
}

interface ScrollContainer {
  scrollTop: number;
  readonly scrollHeight: number;
}

export interface PrependAnchor {
  anchor: AnchorElement | null;
  anchorTop: number | null;
  scrollHeight: number;
}

/** Snapshot before the prepend. `anchor` should be the topmost rendered
 *  item (it must keep its identity across the prepend). */
export function capturePrependAnchor(
  container: ScrollContainer,
  anchor: AnchorElement | null,
): PrependAnchor {
  return {
    anchor,
    anchorTop: anchor ? anchor.getBoundingClientRect().top : null,
    scrollHeight: container.scrollHeight,
  };
}

/** After the DOM holds the prepended rows, shift scrollTop so the anchored
 *  item sits exactly where it was. */
export function restorePrependAnchor(
  container: ScrollContainer,
  captured: PrependAnchor,
): void {
  const { anchor, anchorTop, scrollHeight } = captured;
  if (anchor && anchorTop !== null && anchor.isConnected) {
    container.scrollTop += anchor.getBoundingClientRect().top - anchorTop;
  } else {
    container.scrollTop += container.scrollHeight - scrollHeight;
  }
}
