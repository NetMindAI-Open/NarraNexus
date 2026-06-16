/**
 * @file_name: OnboardingChecklist.tsx
 * @author: NexusAgent
 * @date: 2026-05-21
 * @description: New-user onboarding checklist card (cloud version).
 *
 * Sits at the top of the chat surface. Shows a brand-new cloud user the
 * three things that get them from "registered" to "actually using the
 * product": configure a provider, create a first agent, try a template.
 *
 * Re-entrant by design: the two paths (create agent / apply template) are
 * independent checklist items, not an either/or — completing one leaves
 * the other still actionable. The card hides only when BOTH paths are
 * done, or when the user explicitly dismisses it.
 *
 * State model:
 *  - `first_agent_created` / `template_applied` / `dismissed` come from the
 *    backend (users.metadata.onboarding_progress) — write-once-true.
 *  - `provider_configured` is NOT a stored flag; it is derived live from
 *    the provider count, since that step is gated by SetupPage before this
 *    card is ever shown.
 *  - Row 2 also OR-s in live `agents.length > 0` so creating an agent from
 *    the sidebar reflects instantly without a refetch (the persisted flag
 *    still latches it true even if the agent is later deleted).
 *
 * Cloud-only: renders null outside cloud mode. The progress *flags*
 * themselves are written mode-agnostically (see useCreateAgent /
 * BundleImportPage) — only this card is gated.
 */
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, X, Sparkles, ArrowRight } from 'lucide-react';
import { useConfigStore, useRuntimeStore } from '@/stores';
import { useCreateAgent } from '@/hooks';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import type { OnboardingProgress } from '@/types';

// The templates marketplace lives on the marketing site, not in-app.
// "Apply a template" opens it in a new tab; the user picks a template and
// installs it back into the app via the deep-link / cloud install flow.
const WEBSITE_TEMPLATES_URL = 'https://www.narra.nexus/templates';

interface StepRow {
  key: string;
  label: string;
  hint: string;
  done: boolean;
  /** Action button text — omitted when the step is already done. */
  cta?: string;
  onAction?: () => void;
}

export function OnboardingChecklist() {
  const navigate = useNavigate();
  const mode = useRuntimeStore((s) => s.mode);
  const isCloud = mode === 'cloud-web';
  const userId = useConfigStore((s) => s.userId);
  const agentCount = useConfigStore((s) => s.agents.length);
  const { createAgent, creating } = useCreateAgent();

  const [progress, setProgress] = useState<OnboardingProgress | null>(null);
  const [providerCount, setProviderCount] = useState<number>(0);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (!isCloud || !userId) {
      setLoaded(true);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const [ob, prov] = await Promise.all([
          api.getOnboarding(userId),
          api.getProviders(),
        ]);
        if (cancelled) return;
        if (ob.success && ob.progress) setProgress(ob.progress);
        if (prov.success && prov.data?.providers) {
          setProviderCount(Object.keys(prov.data.providers).length);
        }
      } catch {
        // Network/backend hiccup — leave progress null so the card just
        // doesn't render. Onboarding is a nudge, never a blocker.
      } finally {
        if (!cancelled) setLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isCloud, userId]);

  const dismiss = useCallback(() => {
    setProgress((p) => (p ? { ...p, dismissed: true } : p));
    if (userId) {
      api.markOnboardingStep(userId, 'dismissed').catch(() => {
        /* best-effort */
      });
    }
  }, [userId]);

  const handleCreateAgent = useCallback(async () => {
    const id = await createAgent();
    if (id) setProgress((p) => (p ? { ...p, first_agent_created: true } : p));
  }, [createAgent]);

  const openTemplates = useCallback(() => {
    // Opening the marketplace is NOT completion — `template_applied` flips
    // only when an import actually confirms (see BundleImportPage).
    window.open(WEBSITE_TEMPLATES_URL, '_blank', 'noopener,noreferrer');
  }, []);

  // ---- Gating ----
  if (!isCloud || !loaded || !progress) return null;

  const agentDone = progress.first_agent_created || agentCount > 0;
  const templateDone = progress.template_applied;
  // Hide once both starting paths are done, or on explicit dismiss.
  if (progress.dismissed || (agentDone && templateDone)) return null;

  const providerDone = providerCount > 0;

  const steps: StepRow[] = [
    {
      key: 'provider',
      label: 'Configure an LLM provider',
      hint: 'NetMind Power is the one-key default — or bring your own API key.',
      done: providerDone,
      cta: providerDone ? undefined : 'Open Settings',
      onAction: () => navigate('/app/settings'),
    },
    {
      key: 'agent',
      label: 'Create your first agent',
      hint: 'Start a blank agent and chat to shape who it is.',
      done: agentDone,
      cta: agentDone ? undefined : creating ? 'Creating…' : 'Create agent',
      onAction: handleCreateAgent,
    },
    {
      key: 'template',
      label: 'Or start from a template',
      hint: 'Browse ready-made agent teams on the templates marketplace.',
      done: templateDone,
      cta: templateDone ? undefined : 'Browse templates',
      onAction: openTemplates,
    },
    {
      key: 'bookmarks',
      label: 'Meet your bookmark strip',
      hint: 'Jobs, inbox and your agent’s profile live behind the edge bookmarks on the right — they light up when something changes.',
      // Done once the user has opened the drawer at least once
      // (ChatView writes the flag on first open). Read per render —
      // the checklist re-renders on every navigation anyway.
      done: typeof window !== 'undefined'
        && window.localStorage.getItem('bookmark_drawer_opened_v1') === '1',
    },
  ];

  const completedCount = steps.filter((s) => s.done).length;

  return (
    <div className="px-5 pt-4">
      <div
        className="rounded-[var(--radius-md)] border p-4"
        style={{
          background: 'var(--bg-secondary)',
          borderColor: 'var(--border-default)',
        }}
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-3 mb-3">
          <div className="flex items-center gap-2">
            <Sparkles
              className="w-4 h-4"
              style={{ color: 'var(--accent-primary)' }}
            />
            <span
              className="text-sm font-medium"
              style={{ color: 'var(--text-primary)' }}
            >
              Getting started
            </span>
            <span
              className="text-xs font-mono"
              style={{ color: 'var(--text-tertiary)' }}
            >
              {completedCount}/{steps.length}
            </span>
          </div>
          <button
            type="button"
            onClick={dismiss}
            aria-label="Dismiss getting-started checklist"
            className="p-1 -m-1 transition-colors"
            style={{ color: 'var(--text-tertiary)' }}
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Steps */}
        <ul className="space-y-2">
          {steps.map((step) => (
            <li key={step.key} className="flex items-center gap-3">
              <span
                className={cn(
                  'w-5 h-5 rounded-full border flex items-center justify-center shrink-0',
                )}
                style={{
                  borderColor: step.done
                    ? 'var(--color-success)'
                    : 'var(--border-default)',
                  background: step.done
                    ? 'var(--color-success)'
                    : 'transparent',
                }}
              >
                {step.done && (
                  <Check className="w-3 h-3" style={{ color: '#fff' }} />
                )}
              </span>

              <div className="min-w-0 flex-1">
                <div
                  className="text-sm"
                  style={{
                    color: step.done
                      ? 'var(--text-tertiary)'
                      : 'var(--text-primary)',
                    textDecoration: step.done ? 'line-through' : 'none',
                  }}
                >
                  {step.label}
                </div>
                {!step.done && (
                  <div
                    className="text-xs mt-0.5"
                    style={{ color: 'var(--text-tertiary)' }}
                  >
                    {step.hint}
                  </div>
                )}
              </div>

              {step.cta && (
                <button
                  type="button"
                  onClick={step.onAction}
                  disabled={creating && step.key === 'agent'}
                  className="shrink-0 inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors disabled:opacity-50"
                  style={{
                    background: 'var(--bg-tertiary)',
                    color: 'var(--text-primary)',
                  }}
                >
                  {step.cta}
                  <ArrowRight className="w-3 h-3" />
                </button>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export default OnboardingChecklist;
