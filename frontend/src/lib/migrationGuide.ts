/**
 * @file_name: migrationGuide.ts
 * @author: NetMind.AI
 * @date: 2026-07-30
 * @description: Per-user persistence for the "import lives behind the + button"
 * coach-mark.
 *
 * 2026-08-27: the offer itself moved into the first-run flow
 * ([[WelcomePage]] step 2), so `welcomed` — "has the welcome dialog been shown"
 * — is gone; that question is now answered server-side by
 * `onboarding_progress.landing_completed`. What survives is the coach-mark:
 * when a user SKIPS the import step, something has to tell them where import
 * went, and a bubble pointing at the sidebar "+" is that something.
 *
 * localStorage keyed by userId: the whole feature is local/desktop and
 * per-machine-per-user is the right scope (a shared machine nudges each user
 * once); no backend round-trip for a UI hint.
 *
 * State machine:
 *   coachmarkPending — the user declined import → point them at "+"
 *   coachmarkDone    — they clicked it away, or never needed it → gone for good
 * The bubble shows while (coachmarkPending && !coachmarkDone), so it survives
 * reloads until explicitly dismissed.
 */

export interface MigrationGuideState {
  coachmarkPending: boolean;
  coachmarkDone: boolean;
}

const DEFAULT: MigrationGuideState = {
  coachmarkPending: false,
  coachmarkDone: false,
};

const key = (userId: string) => `nn_migration_guide:${userId}`;

export function readMigrationGuide(userId: string): MigrationGuideState {
  try {
    const raw = localStorage.getItem(key(userId));
    return raw ? { ...DEFAULT, ...JSON.parse(raw) } : { ...DEFAULT };
  } catch {
    return { ...DEFAULT };
  }
}

export function writeMigrationGuide(
  userId: string,
  patch: Partial<MigrationGuideState>,
): MigrationGuideState {
  const next = { ...readMigrationGuide(userId), ...patch };
  try {
    localStorage.setItem(key(userId), JSON.stringify(next));
  } catch {
    /* storage unavailable — degrade to in-memory (hint may re-show next load) */
  }
  return next;
}
