/**
 * @file_name: onboardingGate.ts
 * @author: NetMind.AI
 * @date: 2026-08-27
 * @description: "Does this user still owe the first-run flow?" — asked once per
 * session, cached, and consulted by EVERY protected entry point.
 *
 * Why it isn't just in the root redirect: `/` is only one way into the app. A
 * `?next=/app/chat` login (the deep-link path the website's CTAs and the /pay
 * bounce use), a bookmark, or a plain refresh all land on `/app/*` directly and
 * would have walked straight past a gate that only guarded `/`. A brand-new
 * account skipping [[WelcomePage]] because it happened to arrive by deep link is
 * exactly the bug this file exists to prevent.
 *
 * The answer is server-side (`onboarding_progress.landing_completed`) so a
 * second browser or machine doesn't replay the flow. Cached per userId in module
 * scope because it is asked on every protected mount and the truth only changes
 * when the flow itself writes the flag — which calls `markWelcomeSeen()` so the
 * cache flips without another round-trip (and without a redirect loop).
 *
 * Existing users predate the flag, so the flag is backfilled silently rather
 * than walking a veteran through a newcomer flow. What counts as "existing" is
 * the subtle part: it must be something a HUMAN did, because login itself
 * creates artifacts. The first version counted "any agent or any provider" and
 * therefore misclassified every new account (Owner hit it 2026-08-27, landed
 * straight in the chat):
 *   - login provisions a guide agent for every new user
 *     (backend/onboarding/provisioning.py), so `agents > 0` is true on day zero;
 *   - login auto-registers NetMind provider cards (`_provision_providers` in
 *     backend/routes/auth.py), so `providers > 0` can be too.
 * So both probes now discount what login made: agents OTHER than the guide, and
 * providers the user wired themselves. Which cards login made is the BACKEND's
 * knowledge: each card carries `auto_provisioned` from GET /api/providers,
 * derived next to the provisioners that write them. This file used to keep its
 * own copy of that source list — the day the backend adds a card the copy does
 * not know, every new account would be read as a veteran and the flow would
 * silently disappear for all of them, which is exactly the bug above.
 */

import { api } from './api';
import { pickGuideAgent } from './guideAgent';

/** Resolved answers, per user. `undefined` = not asked yet this session. */
const cache = new Map<string, boolean>();
/** In-flight probes, so N simultaneous protected mounts cause ONE request. */
const inFlight = new Map<string, Promise<boolean>>();

/** True → the user has not finished (or skipped) the welcome flow yet. */
export function owesWelcomeFlow(userId: string): Promise<boolean> {
  const cached = cache.get(userId);
  if (cached !== undefined) return Promise.resolve(cached);
  const running = inFlight.get(userId);
  if (running) return running;

  const probe = (async () => {
    try {
      const res = await api.getOnboarding();
      if (res.success && res.progress?.landing_completed) return false;

      const [humanAgents, ownProviders] = await Promise.all([
        api
          .getAgents()
          .then((r) => {
            const agents = r.agents ?? [];
            const guide = pickGuideAgent(agents);
            // Everything except the one agent login made for them.
            return agents.filter((a) => a.agent_id !== guide?.agent_id).length;
          })
          .catch(() => 0),
        api
          .getProviders()
          .then((r) => {
            const providers = Object.values(r.data?.providers ?? {}) as Array<{
              auto_provisioned?: boolean;
            }>;
            return providers.filter((p) => p?.auto_provisioned !== true).length;
          })
          .catch(() => 0),
      ]);
      if (humanAgents > 0 || ownProviders > 0) {
        // Existing user: record it so this never runs again, then let them
        // straight through. Best-effort — a failed write costs one more probe
        // next session, not a wrong redirect.
        await api.markOnboardingStep(userId, 'landing_completed').catch(() => {});
        return false;
      }
      return true;
    } catch {
      // Backend not answering yet. Never hold a user hostage on onboarding
      // state: let them into the app and ask again next session.
      return false;
    }
  })();

  const settled = probe.then((owes) => {
    cache.set(userId, owes);
    inFlight.delete(userId);
    return owes;
  });
  inFlight.set(userId, settled);
  return settled;
}

/** Called by the flow when it writes the flag, so the gate stops firing at once
 *  — without this, the redirect to /welcome would fight the navigation away
 *  from it until the next reload. */
export function markWelcomeSeen(userId: string): void {
  cache.set(userId, false);
  inFlight.delete(userId);
}

/** Session wipe: the next user must not inherit this user's answer. */
export function clearOnboardingGateCache(): void {
  cache.clear();
  inFlight.clear();
}
