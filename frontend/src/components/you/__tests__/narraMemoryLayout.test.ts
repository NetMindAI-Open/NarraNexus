/**
 * @file_name: narraMemoryLayout.test.ts
 * @author:
 * @date: 2026-08-06
 * @description: Pins the Narra Memory timeline layout math (Base
 * recvoAmUUSjKXs "timeline renders wrong").
 *
 * The inline useMemo math had three ways to draw outside the axis:
 * a lane's bar is given a minimum width AFTER its left edge is placed, so a
 * recently-created storyline sits at left≈100% and pokes past the "now" edge;
 * a timestamp ahead of the client clock (server/client skew) lands past 100%
 * outright; and when every timestamp fails to parse the fallback puts the bar
 * at exactly 100% + min-width. Separately, a data set spanning hours renders
 * four identical day labels ("Aug 6 Aug 6 Aug 6 Aug 6"), which reads as a
 * broken axis. The layout is now a pure function so all four are pinned here.
 */
import { describe, expect, it } from 'vitest';
import { computeTimelineLayout, MIN_BAR_WIDTH_PCT } from '../narraMemoryLayout';
import type { MyNarrative } from '@/types';

const NOW = Date.parse('2026-08-06T12:00:00Z');

function narrative(over: Partial<MyNarrative>): MyNarrative {
  return {
    narrative_id: 'nar_x',
    agent_id: 'agent_x',
    agent_name: 'Agent X',
    type: 'normal',
    is_special: '',
    name: 'Storyline',
    summary: '',
    topic_hint: '',
    topic_keywords: [],
    round_counter: 0,
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-02T00:00:00Z',
    ...over,
  } as MyNarrative;
}

function rightEdge(lane: { left: number; width: number }): number {
  return lane.left + lane.width;
}

describe('computeTimelineLayout — bars stay inside the axis', () => {
  it('keeps a just-created storyline inside the right edge', () => {
    const justNow = new Date(NOW - 1000).toISOString();
    const old = narrative({ narrative_id: 'nar_old' });
    const fresh = narrative({
      narrative_id: 'nar_fresh',
      created_at: justNow,
      updated_at: justNow,
    });
    const layout = computeTimelineLayout([old, fresh], '', NOW);
    for (const lane of layout!.lanes) {
      expect(rightEdge(lane)).toBeLessThanOrEqual(100);
      expect(lane.left).toBeGreaterThanOrEqual(0);
    }
  });

  it('clamps a timestamp ahead of the client clock (server skew)', () => {
    const future = new Date(NOW + 90_000).toISOString();
    const layout = computeTimelineLayout(
      [narrative({ updated_at: future })],
      '',
      NOW,
    );
    expect(rightEdge(layout!.lanes[0])).toBeLessThanOrEqual(100);
  });

  it('keeps unparseable-timestamp fallbacks inside the axis', () => {
    const layout = computeTimelineLayout(
      [narrative({ created_at: 'not-a-date', updated_at: 'not-a-date' })],
      '',
      NOW,
    );
    expect(rightEdge(layout!.lanes[0])).toBeLessThanOrEqual(100);
  });

  it('still grants every bar the minimum visible width', () => {
    const justNow = new Date(NOW - 1000).toISOString();
    const layout = computeTimelineLayout(
      [narrative({ created_at: justNow, updated_at: justNow })],
      '',
      NOW,
    );
    expect(layout!.lanes[0].width).toBeGreaterThanOrEqual(MIN_BAR_WIDTH_PCT);
  });
});

describe('computeTimelineLayout — axis tick labels', () => {
  it('adds time-of-day when the whole span fits inside two days', () => {
    const layout = computeTimelineLayout(
      [
        narrative({
          created_at: new Date(NOW - 6 * 3600_000).toISOString(),
          updated_at: new Date(NOW - 3600_000).toISOString(),
        }),
      ],
      '',
      NOW,
    );
    const labels = layout!.ticks.map((t) => t.label);
    // Day-only labels would be four identical strings; with time-of-day the
    // ticks are distinguishable.
    expect(new Set(labels).size).toBe(labels.length);
  });

  it('keeps ticks distinguishable across a 3-day span', () => {
    // Adjacent ticks sit span/4 = 18h apart here: day-granular labels
    // collide even though the TOTAL span exceeds two days. The formatter
    // must key off the tick gap, not the span.
    const layout = computeTimelineLayout(
      [
        narrative({
          created_at: new Date(NOW - 3 * 86400_000).toISOString(),
          updated_at: new Date(NOW - 3600_000).toISOString(),
        }),
      ],
      '',
      NOW,
    );
    const labels = layout!.ticks.map((t) => t.label);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it('keeps ticks distinguishable across a minute-scale span', () => {
    // A cold-start user's only narrative is seconds old: ticks sit
    // ~20s apart, so minute-precision labels collide too.
    const layout = computeTimelineLayout(
      [
        narrative({
          created_at: new Date(NOW - 90_000).toISOString(),
          updated_at: new Date(NOW - 1000).toISOString(),
        }),
      ],
      '',
      NOW,
    );
    const labels = layout!.ticks.map((t) => t.label);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it('keeps plain day labels for a multi-week span', () => {
    const layout = computeTimelineLayout(
      [
        narrative({
          created_at: new Date(NOW - 30 * 86400_000).toISOString(),
          updated_at: new Date(NOW - 86400_000).toISOString(),
        }),
      ],
      '',
      NOW,
    );
    for (const tick of layout!.ticks) {
      expect(tick.label).not.toMatch(/\d:\d\d/);
    }
  });
});

describe('computeTimelineLayout — existing semantics preserved', () => {
  it('sorts lanes by created_at ascending', () => {
    const a = narrative({ narrative_id: 'a', created_at: '2026-08-03T00:00:00Z' });
    const b = narrative({ narrative_id: 'b', created_at: '2026-08-01T00:00:00Z' });
    const layout = computeTimelineLayout([a, b], '', NOW);
    expect(layout!.lanes.map((l) => l.n.narrative_id)).toEqual(['b', 'a']);
  });

  it('filters by search query across name/summary/topic/agent', () => {
    const a = narrative({ narrative_id: 'a', name: 'Travel plans' });
    const b = narrative({ narrative_id: 'b', name: 'Groceries' });
    const layout = computeTimelineLayout([a, b], 'travel', NOW);
    expect(layout!.lanes.map((l) => l.n.narrative_id)).toEqual(['a']);
  });

  it('returns null for an empty result set', () => {
    expect(computeTimelineLayout([], '', NOW)).toBeNull();
    expect(computeTimelineLayout([narrative({})], 'zzz-no-match', NOW)).toBeNull();
  });
});
