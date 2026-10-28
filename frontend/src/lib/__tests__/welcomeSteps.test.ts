/**
 * Unit tests for the first-run flow's step composition (see lib/welcomeSteps.ts).
 * Two contracts worth pinning: the order never changes (meeting the agent is
 * always last), and a step the user cannot act on is absent rather than shown
 * disabled — cloud in particular must never display an import step.
 */
import { describe, it, expect } from 'vitest';
import {
  buildWelcomeSteps,
  isWelcomeFlowEmpty,
  shouldProbeDetections,
  type WelcomeStepsInput,
} from '../welcomeSteps';

const base: WelcomeStepsInput = {
  mode: 'local',
  detectionCount: 29,
  guideAgentEnabled: true,
};

describe('buildWelcomeSteps', () => {
  it('is model → import → agent for a fresh local machine with agents to import', () => {
    expect(buildWelcomeSteps(base)).toEqual(['model', 'import', 'agent']);
  });

  it('drops the import step on cloud — there is no user filesystem there', () => {
    expect(buildWelcomeSteps({ ...base, mode: 'cloud-web', detectionCount: undefined })).toEqual([
      'model',
      'agent',
    ]);
    // even if a caller wrongly passes detections, cloud still has no import step
    expect(buildWelcomeSteps({ ...base, mode: 'cloud-web', detectionCount: 12 })).toEqual([
      'model',
      'agent',
    ]);
  });

  it('drops the import step when nothing was found on this machine', () => {
    expect(buildWelcomeSteps({ ...base, detectionCount: 0 })).toEqual(['model', 'agent']);
    expect(buildWelcomeSteps({ ...base, detectionCount: undefined })).toEqual(['model', 'agent']);
  });

  it('keeps the model step even when providers already exist', () => {
    // Regression guard for the 2026-08-27 bug: the step was gated on
    // `providerCount === 0`, but login auto-registers NetMind cards, so a
    // brand-new account arrived with two providers and never saw screen one.
    expect(buildWelcomeSteps(base)[0]).toBe('model');
    expect(buildWelcomeSteps({ ...base, mode: 'cloud-web', detectionCount: undefined })[0]).toBe(
      'model',
    );
  });

  it('drops the agent step when the deployment provisions no guide agent', () => {
    expect(buildWelcomeSteps({ ...base, guideAgentEnabled: false })).toEqual(['model', 'import']);
  });

  it('is never empty in practice — the model step always applies', () => {
    // isWelcomeFlowEmpty still has to exist: a future deployment could switch
    // every step off, and the page must redirect instead of rendering a shell.
    const steps = buildWelcomeSteps({ mode: 'cloud-web', guideAgentEnabled: false });
    expect(steps).toEqual(['model']);
    expect(isWelcomeFlowEmpty(steps)).toBe(false);
    expect(isWelcomeFlowEmpty([])).toBe(true);
  });

  it('keeps the agent step last in every combination', () => {
    for (const mode of ['local', 'cloud'] as const) {
      for (const detectionCount of [0, 5]) {
        const steps = buildWelcomeSteps({ ...base, mode, detectionCount });
        expect(steps[steps.length - 1]).toBe('agent');
      }
    }
  });
});

describe('shouldProbeDetections', () => {
  it('only probes the filesystem on local', () => {
    expect(shouldProbeDetections('local')).toBe(true);
    expect(shouldProbeDetections('cloud')).toBe(false);
  });
});
