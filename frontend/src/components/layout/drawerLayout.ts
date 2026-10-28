/**
 * @file_name: drawerLayout.ts
 * @author: NarraNexus
 * @date: 2026-08-19
 * @description: Sizing and persistence rules for the bookmark drawer.
 *
 * Kept apart from MainLayout so the policy (how wide may the drawer get,
 * what does a fresh profile default to) is unit-testable without mounting
 * the whole chat view.
 */

export const DRAWER_PINNED_KEY = 'bookmark_drawer_pinned_v1';
export const DRAWER_OPENED_ONCE_KEY = 'bookmark_drawer_opened_v1';
export const DRAWER_WIDTH_KEY = 'bookmark_drawer_width_v1';
export const DRAWER_FIRST_RUN_KEY = 'bookmark_drawer_first_run_v1';
/** JSON array of agent ids whose chat has been shown at least once on this
 *  desktop — the per-agent "first view" marker (see shouldAutoOpenForAgent). */
export const DRAWER_AGENT_SEEN_KEY = 'bookmark_drawer_agents_seen_v1';
/** Bound on the seen list; the oldest ids fall off first. */
export const MAX_SEEN_AGENTS = 500;

export const DEFAULT_DRAWER_PX = 400;
export const MIN_DRAWER_PX = 300;
// Reserved room the drawer may never eat into: the sidebar (272) plus the
// chat column's minimum (400). Within that, the drawer can grow to 60% of
// the viewport — an artifact wants real estate, and "as wide as half the
// screen" is the whole point of the drawer replacing the skinny side column.
export const DRAWER_VIEWPORT_RESERVE_PX = 672;
export const DRAWER_MAX_VIEWPORT_FRACTION = 0.6;

/** Largest width the pinned drawer may take on this viewport. */
export function maxDrawerPx(viewportW: number): number {
  return Math.max(
    MIN_DRAWER_PX,
    Math.min(viewportW * DRAWER_MAX_VIEWPORT_FRACTION, viewportW - DRAWER_VIEWPORT_RESERVE_PX),
  );
}

export function clampDrawerWidth(px: number, viewportW: number): number {
  return Math.min(maxDrawerPx(viewportW), Math.max(MIN_DRAWER_PX, px));
}

/** Pinned is the default: panels should stay put until the user says
 *  otherwise. Only an explicit unpin (stored '0') turns it off. */
export function readInitialDrawerPinned(storage: Pick<Storage, 'getItem'>): boolean {
  return storage.getItem(DRAWER_PINNED_KEY) !== '0';
}

/**
 * First run only (desktop): should the artifacts panel auto-open with the
 * coach mark? A brand-new user has to SEE where artifacts land before the
 * panel is worth closing — but ONLY a brand-new user: anyone carrying any
 * pre-existing drawer state (opened a panel, chose a pin state, dragged a
 * width) has already found the drawer and must not be re-onboarded when
 * this feature ships. Read-only — render-safe; the caller marks the first
 * run as seen from an effect (markFirstRunSeen), so a discarded render
 * pass cannot burn the marker. Small viewports return false WITHOUT
 * marking, so a phone visit does not spend the desktop coach mark.
 */
export function shouldAutoOpenFirstRun(
  storage: Pick<Storage, 'getItem'>,
  isSmallViewport: boolean,
): boolean {
  try {
    if (isSmallViewport) return false;
    if (storage.getItem(DRAWER_FIRST_RUN_KEY)) return false;
    const isExistingUser =
      storage.getItem(DRAWER_OPENED_ONCE_KEY) !== null ||
      storage.getItem(DRAWER_PINNED_KEY) !== null ||
      storage.getItem(DRAWER_WIDTH_KEY) !== null;
    return !isExistingUser;
  } catch {
    return false;
  }
}

export function markFirstRunSeen(storage: Pick<Storage, 'setItem'>): void {
  try {
    storage.setItem(DRAWER_FIRST_RUN_KEY, '1');
  } catch {
    /* storage unavailable — the coach may show again; harmless */
  }
}

function readSeenAgents(storage: Pick<Storage, 'getItem'>): string[] {
  const raw = storage.getItem(DRAWER_AGENT_SEEN_KEY);
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === 'string') : [];
  } catch {
    return [];
  }
}

/**
 * Per-agent first view (desktop): should this agent's chat open with the
 * drawer on the Artifacts panel? Owner 2026-09-11: every NEW agent — for new
 * and existing users alike — shows the Artifacts panel (pinned, the pin
 * default) the first time its chat opens, so its explainer is on screen from
 * the start. The global first-run coach above only ever reached brand-new
 * users, so an existing user's new agents never showed it.
 *
 * "New" = not in the seen list; after the first view the agent keeps whatever
 * drawer state the user leaves it in. Read-only and render-safe; the caller
 * marks the agent from an effect (markAgentDrawerSeen). Small viewports and
 * an unreadable store return false WITHOUT marking — a phone visit must not
 * spend the desktop first view, and a broken store must not open the drawer
 * on every single view.
 */
export function shouldAutoOpenForAgent(
  storage: Pick<Storage, 'getItem'>,
  agentId: string | null | undefined,
  isSmallViewport: boolean,
): boolean {
  if (!agentId || isSmallViewport) return false;
  try {
    return !readSeenAgents(storage).includes(agentId);
  } catch {
    return false;
  }
}

export function markAgentDrawerSeen(
  storage: Pick<Storage, 'getItem' | 'setItem'>,
  agentId: string,
): void {
  try {
    const seen = readSeenAgents(storage);
    if (seen.includes(agentId)) return;
    seen.push(agentId);
    storage.setItem(DRAWER_AGENT_SEEN_KEY, JSON.stringify(seen.slice(-MAX_SEEN_AGENTS)));
  } catch {
    /* storage unavailable — the agent may open on Artifacts again; harmless */
  }
}
