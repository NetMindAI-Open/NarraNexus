/**
 * @file_name: builderSession.ts
 * @author: NetMind.AI
 * @date: 2026-09-03
 * @description: sessionStorage persistence for the creation studio — the
 * per-agent "studio is open" flag and the skills/channels the conversation
 * recommended but nobody installed or bound.
 *
 * This module is the STORAGE BACKEND only. The reactive source of truth is
 * `stores/studioStore`, which hydrates from here once and writes back through
 * these functions. Components never read this module directly: a render-time
 * sessionStorage read has no subscribers, so a change made elsewhere (the
 * model recommending a skill, the drawer closing the studio) would not
 * re-render anything — that exact bug is why the store exists.
 *
 * sessionStorage rather than localStorage: it survives the reload that router
 * state does not, yet is scoped to the tab, so two studio sessions in two
 * tabs cannot steal each other's state.
 *
 * Recommendations live client-side because they are not agent state — nothing
 * on the server records "an agent was advised to use web-search". Name,
 * description and instructions are different: those are written straight to
 * the agent, so the server is their truth and nothing here shadows them.
 */

const FLAG_PREFIX = 'nn.studio.';
const REC_PREFIX = 'nn.studioRec.';
const VISITED_PREFIX = 'nn.studioVisited.';

/**
 * Skills and channels the builder SUGGESTED but nobody installed or bound.
 * Kept per agent so the outbound envelope can restate them each turn; without
 * that the model re-suggests the same skill every single reply.
 */
export interface StudioRecommendations {
  skill_ids: string[];
  channels: string[];
}

export interface StoredStudioSession {
  /** Agents the studio is open on. */
  open: Record<string, true>;
  /** Agents that entered the studio at some point in this tab and have not
   *  finished it — the studio can be RESUMED on them. Per tab, like the flag:
   *  a different browser tab does not offer the re-entry. Accepted, not a
   *  bug — the same scoping the flag itself has. */
  visited: Record<string, true>;
  recommendations: Record<string, StudioRecommendations>;
}

function storage(): Storage | null {
  try {
    // Private-mode Safari throws on access; SSR/tests may have no window.
    return typeof window === 'undefined' ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

/** Everything persisted for this tab, read once at store creation. */
export function loadStudioSession(): StoredStudioSession {
  const out: StoredStudioSession = { open: {}, visited: {}, recommendations: {} };
  const store = storage();
  if (!store) return out;
  for (let i = 0; i < store.length; i += 1) {
    const key = store.key(i);
    if (!key) continue;
    if (key.startsWith(FLAG_PREFIX)) {
      const agentId = key.slice(FLAG_PREFIX.length);
      if (agentId) out.open[agentId] = true;
    } else if (key.startsWith(VISITED_PREFIX)) {
      const agentId = key.slice(VISITED_PREFIX.length);
      if (agentId) out.visited[agentId] = true;
    } else if (key.startsWith(REC_PREFIX)) {
      const agentId = key.slice(REC_PREFIX.length);
      const rec = parseRecommendations(store.getItem(key));
      if (agentId && rec) out.recommendations[agentId] = rec;
    }
  }
  return out;
}

export function persistStudioFlag(agentId: string, open: boolean): void {
  if (!agentId) return;
  try {
    if (open) storage()?.setItem(`${FLAG_PREFIX}${agentId}`, '1');
    else storage()?.removeItem(`${FLAG_PREFIX}${agentId}`);
  } catch {
    // Quota or private mode — the in-memory store still holds the flag for
    // this page; only the reload guarantee is lost.
  }
}

export function persistStudioVisited(agentId: string, visited: boolean): void {
  if (!agentId) return;
  try {
    if (visited) storage()?.setItem(`${VISITED_PREFIX}${agentId}`, '1');
    else storage()?.removeItem(`${VISITED_PREFIX}${agentId}`);
  } catch {
    // Same as above.
  }
}

export function persistRecommendations(agentId: string, rec: StudioRecommendations | null): void {
  if (!agentId) return;
  try {
    if (rec) storage()?.setItem(`${REC_PREFIX}${agentId}`, JSON.stringify(rec));
    else storage()?.removeItem(`${REC_PREFIX}${agentId}`);
  } catch {
    // Same as above — recommendations are a convenience, never block.
  }
}

function parseRecommendations(raw: string | null): StudioRecommendations | null {
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    const rec = parsed as Partial<StudioRecommendations>;
    return {
      skill_ids: Array.isArray(rec.skill_ids) ? rec.skill_ids.filter((s) => typeof s === 'string') : [],
      channels: Array.isArray(rec.channels) ? rec.channels.filter((s) => typeof s === 'string') : [],
    };
  } catch {
    return null;
  }
}
