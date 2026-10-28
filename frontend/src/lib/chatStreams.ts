/**
 * @file_name: chatStreams.ts
 * @author:
 * @date: 2026-08-21
 * @description: Single source for the chat-tab → history-stream mapping.
 *
 * The conversation and the Activity Log are two independently-paginated
 * backend streams (simple-chat-history `include`). ChatPanel fetches the active
 * stream at three points (first page, load-more, poll); routing that decision
 * through one pure function keeps those points from drifting apart and makes
 * the mapping unit-testable without rendering the whole panel.
 */

export type ChatStream = 'chat' | 'activity';

/** The inner tab IS the Activity Log ('activity'); every other tab reads the
 *  conversation ('chat') stream. */
export function streamForTab(tab: 'conversation' | 'inner'): ChatStream {
  return tab === 'inner' ? 'activity' : 'chat';
}
