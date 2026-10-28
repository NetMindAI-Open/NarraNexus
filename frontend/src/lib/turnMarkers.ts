/**
 * @file_name: turnMarkers.ts
 * @date: 2026-09-11
 * @description: The two no-reply turn markers and their render-time labels.
 *
 * A turn that produced no owner-facing reply is persisted by the backend
 * chat module's persist_turn, in
 * plugins/builtin.chat/src/narranexus_plugins/chat_module/chat_module.py
 * with one of two fixed English markers as its assistant content: the user stopped the turn, or the agent
 * chose silence. The live session (chatStore.stopStreaming) writes the SAME
 * marker, so a settled bubble and its reloaded history row carry identical
 * content, and literal comparisons (buildTimeline's non-chat junk filter,
 * the backend's is_no_response check) keep working. Only the renderer
 * turns a marker into a localized label; the marker itself never changes
 * with the UI language.
 */
import type { TFunction } from 'i18next';

/** Persisted when the user cancelled a turn before any reply went out. */
export const INTERRUPTED_MARKER = '(Interrupted by user)';

/** Persisted when the agent finished a turn without replying. */
export const NO_RESPONSE_MARKER = '(Agent decided no response needed)';

const MARKER_LABEL_KEYS: Record<string, string> = {
  [INTERRUPTED_MARKER]: 'chat.stoppedByUser',
  [NO_RESPONSE_MARKER]: 'chat.noResponseNeeded',
};

/**
 * Localized label for a no-reply marker; any other content passes through
 * unchanged. Exact match only: a real reply that merely quotes a marker
 * is not a marker.
 */
export function localizeTurnMarker(content: string, t: TFunction): string {
  const key = MARKER_LABEL_KEYS[content];
  return key ? t(key) : content;
}
