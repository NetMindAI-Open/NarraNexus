/**
 * Unit tests for buildUnifiedTimeline — the history/session merge + dedup.
 *
 * This logic has been the source of two production bugs (Bug 19 — user
 * retry swallowed; and the "latest reply shown twice" bug). It is now a
 * pure function specifically so these cases can be pinned here.
 */

import { describe, it, expect } from 'vitest';
import { buildUnifiedTimeline } from '../buildTimeline';
import type { SimpleChatMessage } from '@/types/api';
import type { ChatMessage } from '@/types';

// ── builders ──────────────────────────────────────────────────────────
function hist(p: Partial<SimpleChatMessage> & { role: 'user' | 'assistant'; content: string }): SimpleChatMessage {
  return { ...p };
}
function sess(
  p: Partial<ChatMessage> & { id: string; role: 'user' | 'assistant'; content: string; timestamp: number },
): ChatMessage {
  return { ...p };
}

describe('buildUnifiedTimeline', () => {
  it('returns empty for empty inputs', () => {
    expect(buildUnifiedTimeline([], [])).toEqual([]);
  });

  it('carries a session message error state (isError + warnings) onto the timeline item', () => {
    const errored = buildUnifiedTimeline(
      [],
      [sess({ id: 's1', role: 'assistant', content: 'API rate limit exceeded', timestamp: 1000, isError: true })],
    );
    expect(errored[0].isError).toBe(true);

    const warned = buildUnifiedTimeline(
      [],
      [sess({ id: 's2', role: 'assistant', content: 'done', timestamp: 2000, warnings: ['module LLM failed'] })],
    );
    expect(warned[0].warnings).toEqual(['module LLM failed']);
  });

  it('passes history-only and session-only through', () => {
    const h = buildUnifiedTimeline(
      [hist({ role: 'user', content: 'hi', timestamp: '2026-05-14T08:00:00Z' })],
      [],
    );
    expect(h).toHaveLength(1);
    expect(h[0].source).toBe('history');

    const s = buildUnifiedTimeline(
      [],
      [sess({ id: 's1', role: 'assistant', content: 'yo', timestamp: 1000 })],
    );
    expect(s).toHaveLength(1);
    expect(s[0].source).toBe('session');
  });

  // ── THE core regression: "latest reply shown twice" ────────────────
  it('dedups by (role, event_id) even when content drifts by whitespace', () => {
    // Same turn E: history persisted "hello", session assembled "hello "
    // (trailing space) — the old role:content key would MISS and render
    // both. event_id matching collapses them.
    const timeline = buildUnifiedTimeline(
      [
        hist({ role: 'user', content: 'q', event_id: 'E', timestamp: '2026-05-14T08:00:00Z' }),
        hist({ role: 'assistant', content: 'hello', event_id: 'E', timestamp: '2026-05-14T08:00:05Z' }),
      ],
      [
        sess({ id: 'u1', role: 'user', content: 'q', event_id: 'E', timestamp: 1000 }),
        sess({ id: 'a1', role: 'assistant', content: 'hello ', event_id: 'E', timestamp: 5000 }),
      ],
    );
    // Exactly the two history rows — both session copies dropped.
    expect(timeline).toHaveLength(2);
    expect(timeline.every((t) => t.source === 'history')).toBe(true);
    expect(timeline.filter((t) => t.role === 'assistant')).toHaveLength(1);
  });

  it('keeps a session message whose event_id is not in history yet', () => {
    // Turn just finished; history has not reloaded. The session reply
    // must still render (no vanish).
    const timeline = buildUnifiedTimeline(
      [hist({ role: 'user', content: 'q', event_id: 'OLD', timestamp: '2026-05-14T07:00:00Z' })],
      [sess({ id: 'a1', role: 'assistant', content: 'fresh reply', event_id: 'NEW', timestamp: 9000 })],
    );
    expect(timeline).toHaveLength(2);
    expect(timeline.find((t) => t.role === 'assistant')?.source).toBe('session');
  });

  it('does NOT cross-match: same event_id, different role', () => {
    // History has only the USER row of turn E. The session ASSISTANT of
    // turn E must still render — (role,event_id) is the key, not event_id.
    const timeline = buildUnifiedTimeline(
      [hist({ role: 'user', content: 'q', event_id: 'E', timestamp: '2026-05-14T08:00:00Z' })],
      [
        sess({ id: 'u1', role: 'user', content: 'q', event_id: 'E', timestamp: 1000 }),
        sess({ id: 'a1', role: 'assistant', content: 'reply', event_id: 'E', timestamp: 5000 }),
      ],
    );
    // user deduped (history has user:E), assistant kept (history lacks assistant:E)
    expect(timeline).toHaveLength(2);
    expect(timeline.find((t) => t.role === 'assistant')?.source).toBe('session');
    expect(timeline.find((t) => t.role === 'user')?.source).toBe('history');
  });

  it('multi-send turn: one joined session reply collapses against N history rows', () => {
    // Agent sent two messages in turn E → 2 history rows, 1 joined session
    // copy. event_id+role match drops the session copy; both history rows
    // render (no duplication, and arguably more faithful).
    const timeline = buildUnifiedTimeline(
      [
        hist({ role: 'assistant', content: 'part one', event_id: 'E', timestamp: '2026-05-14T08:00:01Z' }),
        hist({ role: 'assistant', content: 'part two', event_id: 'E', timestamp: '2026-05-14T08:00:02Z' }),
      ],
      [sess({ id: 'a1', role: 'assistant', content: 'part one\n\npart two', event_id: 'E', timestamp: 5000 })],
    );
    expect(timeline).toHaveLength(2);
    expect(timeline.every((t) => t.source === 'history')).toBe(true);
  });

  // ── content fallback (event-id-less messages) ──────────────────────
  it('falls back to role:content + window when event_id is absent', () => {
    const ts = Date.parse('2026-05-14T08:00:00Z');
    const timeline = buildUnifiedTimeline(
      [hist({ role: 'user', content: 'legacy', timestamp: '2026-05-14T08:00:00Z' })],
      [sess({ id: 'u1', role: 'user', content: 'legacy', timestamp: ts + 200 })],
    );
    expect(timeline).toHaveLength(1);
    expect(timeline[0].source).toBe('history');
  });

  it('content fallback does NOT dedup outside the time window', () => {
    const ts = Date.parse('2026-05-14T08:00:00Z');
    const timeline = buildUnifiedTimeline(
      [hist({ role: 'user', content: 'legacy', timestamp: '2026-05-14T08:00:00Z' })],
      // 10 min later — way past the 5-min window
      [sess({ id: 'u1', role: 'user', content: 'legacy', timestamp: ts + 600_000 })],
    );
    expect(timeline).toHaveLength(2);
  });

  it('Bug 19: a user retry of identical text is not swallowed', () => {
    // History has ONE "go" (the first attempt, which failed). Session has
    // two "go" — the original AND the retry. Match-and-consume pairs the
    // first with history; the retry survives.
    const ts = Date.parse('2026-05-14T08:00:00Z');
    const timeline = buildUnifiedTimeline(
      [hist({ role: 'user', content: 'go', timestamp: '2026-05-14T08:00:00Z' })],
      [
        sess({ id: 'u1', role: 'user', content: 'go', timestamp: ts + 100 }),
        sess({ id: 'u2', role: 'user', content: 'go', timestamp: ts + 2000 }),
      ],
    );
    expect(timeline).toHaveLength(2);
    // one came from history (the consumed match), one is the surviving retry
    expect(timeline.filter((t) => t.source === 'session')).toHaveLength(1);
  });

  // ── misc ───────────────────────────────────────────────────────────
  it('sorts by timestamp with id as a stable tie-breaker', () => {
    const timeline = buildUnifiedTimeline(
      [],
      [
        sess({ id: 'b', role: 'assistant', content: 'second', timestamp: 2000 }),
        sess({ id: 'a', role: 'user', content: 'first', timestamp: 1000 }),
        sess({ id: 'c', role: 'assistant', content: 'tie', timestamp: 2000 }),
      ],
    );
    expect(timeline.map((t) => t.id)).toEqual(['a', 'b', 'c']);
  });

  it('filters the legacy "(Agent decided no response needed)" junk from non-chat sources', () => {
    const timeline = buildUnifiedTimeline(
      [
        hist({
          role: 'assistant',
          content: '(Agent decided no response needed)',
          working_source: 'job',
          timestamp: '2026-05-14T08:00:00Z',
        }),
        hist({ role: 'assistant', content: 'real', timestamp: '2026-05-14T08:00:01Z' }),
      ],
      [],
    );
    expect(timeline).toHaveLength(1);
    expect(timeline[0].content).toBe('real');
  });
});
