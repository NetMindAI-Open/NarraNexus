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

// ── team rooms ─────────────────────────────────────────────────────────────
//
// The room composer had no draft at all: switching rooms or navigating away
// threw away whatever was half-typed. In a space you are meant to hand work to
// and LEAVE, that is the wrong half of the product to lose.
//
// Its own storage key rather than a prefixed key inside the agent map. Two
// reasons, and the second is the one that decided it: a shared map would make a
// team id and an agent id collide if they can ever be equal (which cannot be
// established from the code either way), and reusing the map with a new key
// FORMAT would orphan every draft a user currently has half-typed — a
// migration's worth of friction to save one localStorage key.

const TEAM_KEY = 'narra-nexus-team-drafts';

function readAllTeams(): Record<string, string> {
  try {
    const raw = localStorage.getItem(TEAM_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, string>) : {};
  } catch {
    return {};
  }
}

/** Read the saved draft for a team room ('' when none). */
export function getTeamDraft(teamId: string): string {
  if (!teamId) return '';
  return readAllTeams()[teamId] ?? '';
}

/** Save (or clear, when empty) the draft for a team room. */
export function setTeamDraft(teamId: string, value: string): void {
  if (!teamId) return;
  const map = readAllTeams();
  if (value) {
    map[teamId] = value;
  } else {
    delete map[teamId];
  }
  try {
    localStorage.setItem(TEAM_KEY, JSON.stringify(map));
  } catch {
    /* quota exceeded / storage disabled — drafts are best-effort */
  }
}
