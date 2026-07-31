/**
 * @file_name: migrationGuide.ts
 * @author: NetMind.AI
 * @date: 2026-07-30
 * @description: Per-user persistence for the one-time "import your other agents"
 * guided flow (MigrationGuide).
 *
 * Keyed by userId so it is per-user (a shared machine nudges each user once).
 * localStorage — the whole feature is local/desktop only, so per-machine-per-user
 * is the right scope; no backend round-trip.
 *
 * State machine:
 *   welcomed          — the welcome modal has been shown+actioned once. Never again.
 *   coachmarkPending  — user dismissed the modal via Later/X → point them at "+".
 *   coachmarkDone     — user dismissed the coach-mark → gone for good.
 * The coach-mark shows while (welcomed && coachmarkPending && !coachmarkDone), so
 * it survives reloads until explicitly clicked away.
 */

export interface MigrationGuideState {
  welcomed: boolean;
  coachmarkPending: boolean;
  coachmarkDone: boolean;
}

const DEFAULT: MigrationGuideState = {
  welcomed: false,
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
    /* storage unavailable — degrade to in-memory (nudge may re-show next load) */
  }
  return next;
}
