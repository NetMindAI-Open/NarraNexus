/**
 * Unit test for streamForTab — the single source of the chat-tab → history
 * stream mapping used by ChatPanel's three fetch points (first page, load-more,
 * poll). Pins that the inner tab reads the Activity Log stream and every other
 * tab reads the conversation stream, so those fetch points cannot drift.
 */
import { describe, it, expect } from 'vitest';
import { streamForTab } from '../chatStreams';

describe('streamForTab', () => {
  it('maps the inner tab to the activity stream', () => {
    expect(streamForTab('inner')).toBe('activity');
  });

  it('maps the conversation tab to the chat stream', () => {
    expect(streamForTab('conversation')).toBe('chat');
  });
});
