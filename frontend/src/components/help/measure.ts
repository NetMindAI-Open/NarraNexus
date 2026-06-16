/**
 * @file_name: measure.ts
 * @author:
 * @date: 2026-06-11
 * @description: Anchor measurement + rail layout for the help overlay.
 *
 * Measurement: querySelector by data-help-id, skip missing / zero-size /
 * fully-offscreen anchors, sort by priority.
 *
 * Layout (round-4 rules):
 *  - Rails sit at FIXED screen-edge offsets — never derived from target
 *    extents, so a huge anchor (artifact column, conversation area)
 *    cannot drag a rail across the screen.
 *  - Arrows aim at the point on the target's BORDER nearest the note
 *    (then back off a few px), so wide targets get short edge-touching
 *    arrows instead of screen-crossing ones.
 *  - The arrow leaves the note at its headline's vertical center, on
 *    the side facing the target; note text aligns toward that side so
 *    text and arrow read as one gesture.
 *  - When several notes point into a tight vertical cluster (the
 *    strip), each leader gets its own LANE in the corridor between
 *    rail and targets — vertical runs never overlap (round-5 fix for
 *    "arrows all vertical and indistinguishable").
 */

import type { HelpAnnotation } from './helpContent';

export interface MeasuredAnnotation extends HelpAnnotation {
  rect: { x: number; y: number; width: number; height: number };
}

export interface PlacedAnnotation extends MeasuredAnnotation {
  noteX: number;
  noteY: number;
  noteW: number;
  /** Text alignment toward the arrow side. */
  align: 'left' | 'right' | 'center';
  from: { x: number; y: number };
  to: { x: number; y: number };
  /** When set, draw an elbow leader through this lane instead of a
   *  direct arrow (used when the vertical travel is large). */
  laneX?: number;
  /**
   * Stroke language (round-6):
   *  - 'point': arrow/leader + optional circle — for CONTROLS.
   *  - 'region': no arrow, no circle — the note sits ON the area it
   *    describes with a short underline. Auto-selected for large
   *    targets; circling a full-width composer drew two parallel
   *    lines across the screen, and arrows into a region's far border
   *    were pure noise.
   */
  kind: 'point' | 'region';
  /** Underline segment for 'region' notes: centered x, y, width. */
  underline?: { x: number; y: number; width: number };
}

export function measureAnnotations(
  annotations: HelpAnnotation[],
): MeasuredAnnotation[] {
  const out: MeasuredAnnotation[] = [];
  for (const a of annotations) {
    const el = document.querySelector(`[data-help-id="${a.helpId}"]`);
    if (!el) continue;
    const r = (el as HTMLElement).getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    if (
      r.right < 0 ||
      r.bottom < 0 ||
      r.left > window.innerWidth ||
      r.top > window.innerHeight
    ) {
      continue;
    }
    out.push({ ...a, rect: { x: r.x, y: r.y, width: r.width, height: r.height } });
  }
  return out.sort((a, b) => a.priority - b.priority);
}

const NOTE_W = 310;
/** Estimated note height: headline + optional detail (wrapped).
 *  Metrics track HelpOverlay's type scale (26px note / 19px detail). */
function estimateHeight(a: MeasuredAnnotation): number {
  const headline = 38;
  if (!a.detail) return headline + 12;
  const lines = Math.ceil(a.detail.length / 30);
  return headline + lines * 24 + 12;
}

/** Nearest point on a rect's border to `p`, backed off by `inset` px. */
function nearestBorderPoint(
  p: { x: number; y: number },
  rect: { x: number; y: number; width: number; height: number },
  inset: number,
): { x: number; y: number } {
  let bx = Math.max(rect.x, Math.min(p.x, rect.x + rect.width));
  let by = Math.max(rect.y, Math.min(p.y, rect.y + rect.height));
  const inside =
    p.x > rect.x && p.x < rect.x + rect.width &&
    p.y > rect.y && p.y < rect.y + rect.height;
  if (inside) {
    // Note overlaps a huge target — exit through the nearer vertical edge.
    const toLeft = p.x - rect.x;
    const toRight = rect.x + rect.width - p.x;
    bx = toLeft < toRight ? rect.x : rect.x + rect.width;
    by = p.y;
  }
  // Back off along the border→note direction so the tip floats just
  // outside the control.
  const dx = p.x - bx;
  const dy = p.y - by;
  const len = Math.max(1, Math.hypot(dx, dy));
  return { x: bx + (dx / len) * inset, y: by + (dy / len) * inset };
}

/**
 * Place every measured annotation. Pure given (annotations, viewport).
 */
export function layoutAnnotations(
  measured: MeasuredAnnotation[],
  vw: number,
  vh: number,
): PlacedAnnotation[] {
  const placed: PlacedAnnotation[] = [];
  const GAP = 22;
  const FOOTER = 140; // keep clear of the centered controls
  const HEADER = 84;  // keep clear of the top-center page title
  const isRegion = (m: MeasuredAnnotation) =>
    m.rect.width > vw * 0.38 || m.rect.height > vh * 0.5;

  // ── Region notes: written ON the area, short underline, no strokes ──
  for (const m of measured.filter(isRegion)) {
    const cx = m.rect.x + m.rect.width / 2;
    const noteX = Math.max(16, Math.min(cx - NOTE_W / 2, vw - NOTE_W - 16));
    const isComposerLike = m.rect.y > vh * 0.7; // bottom strip → write above
    const h = estimateHeight(m);
    const noteY = isComposerLike
      ? Math.max(HEADER, m.rect.y - h - 26)
      : Math.max(HEADER, m.rect.y + Math.min(72, m.rect.height * 0.12));
    placed.push({
      ...m,
      noteX,
      noteY,
      noteW: NOTE_W,
      align: 'center',
      from: { x: cx, y: noteY },
      to: { x: cx, y: noteY },
      kind: 'region',
      underline: { x: cx, y: noteY + 34, width: 170 },
    });
  }
  const pointable = measured.filter((m) => !isRegion(m));

  // Fixed rail columns (clamped for narrow windows). Rails sit a full
  // corridor away from their target edges so leader lanes have room.
  const railX: Record<'left' | 'right', number> = {
    left: Math.min(Math.max(vw * 0.26, 320), vw * 0.42),
    right: Math.max(vw - NOTE_W - 180, vw * 0.5),
  };

  for (const rail of ['left', 'right'] as const) {
    const items = pointable
      .filter((m) => m.rail === rail)
      .sort((a, b) => a.rect.y - b.rect.y);
    const noteX = railX[rail];

    // Proximity column: each note sits as close to its target's height
    // as the stack allows (monotonic push-down keeps the column tidy
    // and — because both notes and targets are sorted by Y — leaders
    // can never cross). Round 8: the centered legend stranded the Cost
    // note far from its chip.
    let cursorY = HEADER;
    let prevToY = -Infinity;
    items.forEach((m, idx) => {
      const h = estimateHeight(m);
      const targetY = m.rect.y + m.rect.height / 2;
      const noteY = Math.min(
        Math.max(cursorY, targetY - h / 2, HEADER),
        vh - FOOTER - h,
      );
      cursorY = noteY + h + GAP;

      // Arrow leaves at the headline's vertical center, on the side
      // facing the target; text aligns to that side.
      const headlineMidY = noteY + 19;
      const from = {
        x: rail === 'left' ? noteX - 8 : noteX + NOTE_W + 8,
        y: headlineMidY,
      };
      let to = nearestBorderPoint(from, m.rect, 8);

      // Large vertical travel → elbow leader through a per-item lane,
      // entering the target HORIZONTALLY at its center height.
      let laneX: number | undefined;
      if (Math.abs(to.y - from.y) > 40) {
        const entryX =
          rail === 'right'
            ? m.rect.x - 8
            : m.rect.x + m.rect.width + 8;
        to = { x: entryX, y: targetY };
        laneX =
          rail === 'right'
            ? Math.min(from.x + 26 + idx * 16, entryX - 18)
            : Math.max(from.x - 26 - idx * 16, entryX + 18);
      }

      // Same-row targets (e.g. adjacent toolbar buttons) would put two
      // entry segments on ONE horizontal line — indistinguishable.
      // Step each subsequent entry down 12px (round 8).
      if (to.y - prevToY < 14) {
        to = { ...to, y: prevToY + 12 };
      }
      prevToY = to.y;
      placed.push({
        ...m,
        noteX,
        noteY,
        noteW: NOTE_W,
        align: rail === 'left' ? 'left' : 'right',
        from,
        to,
        laneX,
        kind: 'point',
      });
    });
  }

  // 'top' rail: note centered above its anchor; when the anchor hugs
  // the top edge (cost chip), there is no room above — sit just BELOW
  // it instead with a short arrow up (round 9: "cost note needn't be
  // far, put it next to the button").
  for (const m of pointable.filter((mm) => mm.rail === 'top')) {
    const h = estimateHeight(m);
    const cx = m.rect.x + m.rect.width / 2;
    const noteX = Math.max(16, Math.min(cx - NOTE_W / 2, vw - NOTE_W - 16));
    const roomAbove = m.rect.y - 90 - h >= HEADER;
    const noteY = roomAbove
      ? m.rect.y - 90 - h
      : Math.min(m.rect.y + m.rect.height + 22, vh - FOOTER - h);
    const from = roomAbove
      ? { x: cx, y: noteY + h }
      : { x: cx, y: noteY - 4 };
    placed.push({
      ...m,
      noteX,
      noteY,
      noteW: NOTE_W,
      align: 'center',
      from,
      to: nearestBorderPoint(from, m.rect, 6),
      kind: 'point',
    });
  }

  return placed;
}
