/**
 * @file_name: welcomeSteps.ts
 * @author: NetMind.AI
 * @date: 2026-08-27
 * @description: Which steps the first-run welcome flow shows, in order. A pure
 * function so "does a cloud user see the import step?" is a unit test, not a
 * thing you discover by logging into production.
 *
 * The order is FIXED — model → import → agent (Owner decision 2026-08-27):
 * meeting the guide agent lands last so the closing CTA drops the user into a
 * conversation that actually works (model wired, history imported), and so the
 * agent's fire-and-forget server-side provisioning has had two screens' worth of
 * time to finish.
 *
 * Steps are DROPPED, never disabled — a step the user can't act on is worse
 * than one that isn't there:
 *   - cloud has no user filesystem (`/detect` 503s) → no import step, and the
 *     caller must not even call detect;
 *   - nothing found on this machine → no import step;
 *   - guide provisioning off for this deployment → no agent step.
 *
 * The model step is NOT conditional on the provider count (2026-08-27 fix). It
 * used to be `providerCount === 0`, which in practice meant the step never
 * appeared: login auto-registers NetMind cards (free tier and/or the user's own
 * Power account — `_provision_providers` in backend/routes/auth.py), so a
 * brand-new account already has two providers before it reaches the flow, and
 * those cards are indistinguishable from a key the user pasted themselves.
 * Since the flow only ever runs for a genuinely new account (existing users are
 * backfilled by [[onboardingGate]]), "show it and let them skip in one click" is
 * both simpler and what the Owner expects to see on screen one.
 */

import type { AppMode } from '@/types/platform';

export type WelcomeStepId = 'model' | 'import' | 'agent';

export interface WelcomeStepsInput {
  /** Deployment mode — the app's own vocabulary, not a private local/cloud
   *  re-encoding: a third AppMode must fail these branches loudly rather than
   *  be silently mapped onto 'local' and probe a filesystem it has no right
   *  to. Cloud never imports (no user filesystem). */
  mode: AppMode;
  /** Detections from `/api/migrate/detect`; undefined = not probed (cloud). */
  detectionCount?: number;
  /** Whether this deployment provisions a guide agent at all. */
  guideAgentEnabled: boolean;
}

/** Canonical order; the builder only ever filters this list. */
const STEP_ORDER: WelcomeStepId[] = ['model', 'import', 'agent'];

export function buildWelcomeSteps({
  mode,
  detectionCount,
  guideAgentEnabled,
}: WelcomeStepsInput): WelcomeStepId[] {
  const include: Record<WelcomeStepId, boolean> = {
    model: true,
    import: mode === 'local' && (detectionCount ?? 0) > 0,
    agent: guideAgentEnabled,
  };
  return STEP_ORDER.filter((id) => include[id]);
}

/** True when the flow has nothing to show — the caller should skip straight to
 *  the app (and still write `landing_completed`, so it never asks again). */
export const isWelcomeFlowEmpty = (steps: WelcomeStepId[]): boolean => steps.length === 0;

/** Should the caller run `/api/migrate/detect` at all? Cloud must not: the
 *  endpoint 503s there, and a failed probe on the first screen after signup is
 *  a bad first impression for a step that can never appear. */
export const shouldProbeDetections = (mode: AppMode): boolean => mode === 'local';
