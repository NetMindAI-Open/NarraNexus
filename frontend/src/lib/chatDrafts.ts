/**
 * Per-agent chat input drafts.
 *
 * The chat composer keeps a single local `input` state, so switching agents
 * (or reloading the page) used to drop whatever the user had half-typed. We
 * persist drafts keyed by agentId in localStorage so a draft survives BOTH an
 * agent switch and a full reload. Empty drafts are removed rather than stored.
 */

const KEY = 'narra-nexus-chat-drafts';

function readAll(): Record<string, string> {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, string>) : {};
  } catch {
    return {};
  }
}

function writeAll(map: Record<string, string>): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(map));
  } catch {
    /* quota exceeded / storage disabled — drafts are best-effort */
  }
}

/** Read the saved draft for an agent ('' when none). */
export function getChatDraft(agentId: string): string {
  if (!agentId) return '';
  return readAll()[agentId] ?? '';
}

/** Save (or clear, when empty) the draft for an agent. */
export function setChatDraft(agentId: string, value: string): void {
  if (!agentId) return;
  const map = readAll();
  if (value) {
    map[agentId] = value;
  } else {
    delete map[agentId];
  }
  writeAll(map);
}
