/**
 * @file_name: guideCoachmark.ts
 * @author: Bin Liang
 * @date: 2026-08-19
 * @description: Tiny localStorage gate for the one-shot "you can create your
 * own agent" coachmark shown to brand-new users (whose first agent — the
 * onboarding guide agent — was auto-provisioned server-side, so they never
 * met the create button). Lives in lib/ because both the api layer (which
 * learns `is_new_user` from the login response) and the coachmark component
 * need it, and api.ts must not import from components/.
 *
 * States: absent (never a new user here) → 'pending' (show) → 'done'.
 */

const KEY = 'nx-guide-coachmark';

export function markGuideCoachmarkPending(): void {
  try {
    // Never downgrade 'done' back to 'pending' (a re-login of the same new
    // user must not resurrect a dismissed coachmark).
    if (window.localStorage.getItem(KEY) !== 'done') {
      window.localStorage.setItem(KEY, 'pending');
    }
  } catch {
    /* storage unavailable — the greeting text still carries the hint */
  }
}

export function isGuideCoachmarkPending(): boolean {
  try {
    return window.localStorage.getItem(KEY) === 'pending';
  } catch {
    return false;
  }
}

export function dismissGuideCoachmark(): void {
  try {
    window.localStorage.setItem(KEY, 'done');
  } catch {
    /* non-fatal */
  }
}
