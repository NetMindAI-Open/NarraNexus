/**
 * @file_name: WelcomePage.tsx
 * @author: NetMind.AI
 * @date: 2026-08-27
 * @description: The first-run flow — one page, a left rail of steps, one thing
 * per screen: wire a model → import the agents already on this machine → meet
 * the guide agent, then straight into its conversation.
 *
 * Replaces three disconnected surfaces (Owner decision 2026-08-27): the
 * /setup provider page, the import offer that popped over the chat, and the
 * coach-mark that announced the auto-provisioned guide agent. A new user used to
 * cross all three with no sense of where they were.
 *
 * Composition is DATA-DRIVEN, never branched per deployment: [[welcomeSteps]]
 * returns the steps that apply (cloud has no importable filesystem, a configured
 * user needs no model step, …). One empty result means "nothing to onboard" —
 * the page marks the flow done and gets out of the way rather than showing an
 * empty shell.
 *
 * `landing_completed` is written server-side (users.metadata, write-once-true)
 * on ANY exit — finished, skipped, or nothing-to-do — so the flow never replays,
 * not even from a different browser. See [[api_schema]]'s OnboardingProgress.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  StepAgent,
  StepImport,
  StepModel,
  WelcomeRail,
  type WelcomeRailStep,
} from '@/components/welcome';
import { pickGuideAgent } from '@/lib/guideAgent';
import type { AppMode } from '@/types/platform';
import { useAgentImported } from '@/hooks';
import { api } from '@/lib/api';
import { dismissGuideCoachmark, isGuideCoachmarkPending } from '@/lib/guideCoachmark';
import { writeMigrationGuide } from '@/lib/migrationGuide';
import { wipeAllSessionData } from '@/lib/sessionWipe';
import { buildWelcomeSteps, shouldProbeDetections, type WelcomeStepId } from '@/lib/welcomeSteps';
import { markWelcomeSeen } from '@/lib/onboardingGate';
import { isSafeReturnTo } from '@/lib/safe-return';
import { captureProductEvent } from '@/lib/productAnalytics';
import { useChatStore, useConfigStore, useRuntimeStore } from '@/stores';
import type { AgentInfo, FrameworkDetection, MigrationApplyResult } from '@/types';

/** Full-screen spinner while the probe runs. Deliberately a local copy of
 *  App.tsx's private PageFallback — one shared spinner component is a separate
 *  cleanup, and importing across pages/App would be a circular import. */
function WelcomeFallback() {
  return (
    <div className="flex h-dvh-safe w-screen items-center justify-center bg-[var(--bg-deep)]">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-[var(--accent-primary)] border-t-transparent" />
    </div>
  );
}

interface Probe {
  steps: WelcomeStepId[];
  detections: FrameworkDetection[];
  agents: AgentInfo[];
}

export function WelcomePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  // ProtectedRoute redirects here with ?next=<the URL the user was heading to>
  // (a website deep link, a bookmark, a refresh). The flow runs first, then
  // hands them back — otherwise the gate would cost them their destination.
  const nextParam = params.get('next');
  const destination = isSafeReturnTo(nextParam) ? nextParam : '/app/chat';
  // ProtectedRoute never renders this before the mode has resolved, so a
  // null here is not a real state; local is the only mode with anything
  // extra to probe, and the step builder speaks AppMode directly.
  const mode: AppMode = useRuntimeStore((s) => s.mode) ?? 'local';
  const userId = useConfigStore((s) => s.userId);
  const onImported = useAgentImported();

  const [probe, setProbe] = useState<Probe | null>(null);
  const [index, setIndex] = useState(0);
  /** Imports done in this run, so a "Close"-style exit still refreshes them in. */
  const importedRef = useRef<MigrationApplyResult[]>([]);
  const completedRef = useRef(false);
  const enteredFired = useRef(false);

  // ── one-time probe: what does this user still need? ──────────────────────
  useEffect(() => {
    let alive = true;
    const run = async () => {
      const [agents, detections] = await Promise.all([
        api.getAgents().then((r) => r.agents ?? []).catch(() => [] as AgentInfo[]),
        // Cloud has no user filesystem — /detect 503s there, so don't ask.
        shouldProbeDetections(mode)
          ? api.migrateDetect().then((r) => r.detections).catch(() => [] as FrameworkDetection[])
          : Promise.resolve([] as FrameworkDetection[]),
      ]);
      if (!alive) return;
      const steps = buildWelcomeSteps({
        mode,
        detectionCount: detections.length,
        // Either the guide agent is already here, or login told us one is
        // coming (is_new_user AND the server's provisioning switch is on).
        guideAgentEnabled: Boolean(pickGuideAgent(agents)) || isGuideCoachmarkPending(),
      });
      setProbe({ steps, detections, agents });
    };
    void run();
    return () => {
      alive = false;
    };
  }, [mode]);

  useEffect(() => {
    if (enteredFired.current || !probe) return;
    enteredFired.current = true;
    captureProductEvent('welcome_entered');
  }, [probe]);

  /** Write the server-side flag once, then leave. `to` is where the user lands. */
  const leave = useCallback(
    async (reason: 'completed' | 'skipped' | 'nothing_to_do', to: string) => {
      if (!completedRef.current) {
        completedRef.current = true;
        captureProductEvent(reason === 'skipped' ? 'welcome_skipped' : 'welcome_completed');
        if (userId) {
          // Flip the session cache FIRST: ProtectedRoute is about to re-run its
          // gate on the destination route, and a stale "still owes it" answer
          // would bounce the user straight back here.
          markWelcomeSeen(userId);
          // Best-effort write: a failure means the flow may show once more,
          // which is far better than blocking the user's first minute on it.
          await api.markOnboardingStep(userId, 'landing_completed').catch(() => {});
        }
      }
      if (importedRef.current.length > 0) {
        // Refresh the sidebar so imported agents are there on arrival; never
        // navigate from here — `to` already decides where the user goes.
        await onImported(importedRef.current, { open: false });
      }
      navigate(to, { replace: true });
    },
    [navigate, onImported, userId],
  );

  // Nothing to onboard (configured cloud account, no guide agent): don't show
  // an empty flow, just record it and move on.
  useEffect(() => {
    if (probe && probe.steps.length === 0) void leave('nothing_to_do', destination);
  }, [probe, leave, destination]);

  if (!probe || probe.steps.length === 0) return <WelcomeFallback />;

  const steps = probe.steps;
  const current = steps[Math.min(index, steps.length - 1)];
  const isLast = index >= steps.length - 1;

  const advance = () => {
    if (isLast) {
      void leave('completed', destination);
      return;
    }
    setIndex((i) => i + 1);
  };

  /** Skip THIS step. Skipping the last one ends the flow (still marked done —
   *  the user has seen the offer and said no; asking again is nagging). */
  const skipStep = () => {
    if (isLast) {
      void leave('skipped', destination);
      return;
    }
    setIndex((i) => i + 1);
  };

  /** Leave the whole flow from any step. */
  const skipAll = () => void leave('skipped', destination);

  const back = index > 0 ? () => setIndex((i) => Math.max(0, i - 1)) : undefined;

  const openGuideAgent = (agent: AgentInfo | null) => {
    if (agent) {
      // The flow introduced the agent by name, so the coach-mark that exists to
      // announce it has nothing left to say.
      dismissGuideCoachmark();
      useConfigStore.getState().setAgentId(agent.agent_id);
      useChatStore.getState().setActiveAgent(agent.agent_id);
      // Meeting the agent ends in ITS conversation, whatever ?next= said —
      // the CTA promised a chat with this agent.
      void leave('completed', '/app/chat');
      return;
    }
    void leave('completed', destination);
  };

  // The rail names the guide agent as soon as we know it — "Meet Wren" reads
  // like a promise, "Meet your agent" reads like a placeholder.
  const guideName = pickGuideAgent(probe.agents)?.name?.trim() || t('pages.welcome.agent.fallbackName');
  const railSteps: WelcomeRailStep[] = steps.map((id) => ({
    id,
    title: t(`pages.welcome.rail.${id}.title`, { name: guideName }),
    detail:
      id === 'import'
        ? t('pages.welcome.rail.import.detail', { count: probe.detections.length })
        : t(`pages.welcome.rail.${id}.detail`),
  }));

  const handleLogout = () => {
    wipeAllSessionData();
    // Full document load, never a soft navigate — see lib/sessionWipe.
    window.location.href = '/login';
  };

  return (
    <div className="flex h-dvh-safe w-screen flex-col overflow-hidden bg-[var(--nm-card)] md:flex-row">
      <WelcomeRail steps={railSteps} activeIndex={index} onLogout={handleLogout} />

      {current === 'model' && (
        <StepModel onDone={advance} onSkip={skipStep} onBack={back} />
      )}

      {current === 'import' && (
        <StepImport
          detections={probe.detections}
          onDone={(results) => {
            importedRef.current = [...importedRef.current, ...results];
            advance();
          }}
          onSkip={() => {
            // Declining import is fine, but the user should still learn where
            // it lives — arm the sidebar "+" coach-mark ([[MigrationGuide]]).
            if (userId) writeMigrationGuide(userId, { coachmarkPending: true });
            skipStep();
          }}
          onBack={back}
        />
      )}

      {current === 'agent' && (
        <StepAgent
          initialAgents={probe.agents}
          onDone={openGuideAgent}
          onSkip={skipAll}
          onBack={back}
        />
      )}
    </div>
  );
}

export default WelcomePage;
