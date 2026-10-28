/**
 * @file_name: StepModel.tsx
 * @author: NetMind.AI
 * @date: 2026-08-27
 * @description: Welcome step 1 — wire one model. Thin wrapper: the actual
 * picker is [[OneKeyOnboard]], the same card Settings → Providers uses, so the
 * two can never drift into different provider lists.
 *
 * `hideHeader` + `bare` strip the card's own heading and surface — the step
 * frame already supplies both.
 *
 * The collapsed "Advanced setup" fold carries what one pasted key CANNOT
 * express, and it leads with SUBSCRIPTION SIGN-IN (inherited from the retired
 * /setup page, P0 2026-08-28): a Claude Code / Codex subscription needs no API
 * key at all, and while that path was buried inside ProviderSettings' add
 * modal, subscription-only users read first-run as "API key required" and
 * stopped. Two cloud gates, both needed — `mode !== 'cloud-web'` here (fast,
 * hides the heading too; a negative match so a not-yet-hydrated null mode
 * fails OPEN to the local fix), and SubscriptionConnect self-gates on the
 * status route's `allowed` flag (authoritative, covers every caller).
 *
 * Connecting a subscription does NOT advance the flow: the user decides when
 * to leave, exactly as the old page's footer did.
 *
 * Skippable on purpose (Owner decision 2026-08-27): a user without a key must
 * not be trapped on screen one. The cost is a local user who can't send a
 * message yet, which [[MainLayout]]'s "no model wired" notice picks up.
 */

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronRight } from 'lucide-react';
import { OneKeyOnboard } from '@/components/settings/OneKeyOnboard';
import { ProviderSettings } from '@/components/settings/ProviderSettings';
import { SubscriptionConnect } from '@/components/settings/SubscriptionConnect';
import { api } from '@/lib/api';
import { addProviderCard, type ProviderRow } from '@/lib/providersApi';
import { useRuntimeStore } from '@/stores';
import { WelcomeStepFrame } from './WelcomeStepFrame';

export interface StepModelProps {
  onDone: () => void;
  onSkip: () => void;
  onBack?: () => void;
}

export function StepModel({ onDone, onSkip, onBack }: StepModelProps) {
  const { t } = useTranslation();
  const mode = useRuntimeStore((s) => s.mode);
  const [advanced, setAdvanced] = useState(false);
  // Provider records feeding SubscriptionConnect ("Added ✓" vs the
  // Add-as-Provider button).
  const [providers, setProviders] = useState<Record<string, ProviderRow>>({});
  const [subError, setSubError] = useState('');
  // Bumped after every successful add through the subscription card so
  // ProviderSettings (which owns its own provider list) refetches too —
  // otherwise "Your providers" shows stale data until remount.
  const [providersVersion, setProvidersVersion] = useState(0);

  const probe = async () => {
    try {
      const data = await api.getProviders();
      if (data.success && data.data?.providers) setProviders(data.data.providers);
    } catch {
      // Backend not ready — the step still works as a key-paste surface.
    }
  };

  // react-hooks/set-state-in-effect flags probe's setState, but it happens
  // after an await — never synchronously in the effect body.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    probe();
  }, []);

  const addProvider = async (body: Record<string, unknown>): Promise<boolean> => {
    setSubError('');
    const res = await addProviderCard(body, t);
    if (!res.ok) {
      setSubError(res.error);
      return false;
    }
    await probe();
    setProvidersVersion((v) => v + 1);
    return true;
  };

  const toggleAdvanced = () => {
    // A stale subscription error from a previous attempt should not greet
    // the user on re-expand.
    setSubError('');
    setAdvanced((v) => !v);
  };

  return (
    <WelcomeStepFrame
      title={t('pages.welcome.model.title')}
      onBack={onBack}
      skipLabel={t('pages.welcome.model.skip')}
      onSkip={onSkip}
    >
      {/* OneKeyOnboard owns its own submit button — the frame contributes no
          CTA here, or the step would show two primaries. */}
      <OneKeyOnboard hideHeader bare onComplete={onDone} />

      {/* The escape hatch the old /setup page had: subscription sign-in,
          custom endpoints and per-slot models can't be expressed as one
          pasted key, and sending those users to Settings before they've
          finished signing up is a dead end. Collapsed, because it is the
          minority path. */}
      <button
        type="button"
        onClick={toggleAdvanced}
        className="mt-4 inline-flex items-center gap-1.5 text-xs text-[var(--nm-ink50)] hover:text-[var(--nm-ink)]"
      >
        <ChevronRight
          className={`h-3.5 w-3.5 transition-transform ${advanced ? 'rotate-90' : ''}`}
        />
        {t('pages.welcome.model.advanced')}
      </button>
      {advanced && (
        <div className="mt-3 flex flex-col gap-4 border-t border-[var(--nm-hairline)] pt-3">
          {mode !== 'cloud-web' && (
            <div className="flex flex-col gap-3">
              <div>
                <div className="text-sm font-medium text-[var(--nm-ink)]">
                  {t('pages.welcome.model.subscriptionTitle')}
                </div>
                <p className="mt-0.5 text-xs text-[var(--nm-ink70)]">
                  {t('pages.welcome.model.subscriptionSubtitle')}
                </p>
              </div>
              {subError && (
                <p className="text-xs" role="alert" style={{ color: 'var(--color-error)' }}>
                  {subError}
                </p>
              )}
              <SubscriptionConnect
                providers={Object.values(providers)}
                addProvider={addProvider}
              />
            </div>
          )}
          <ProviderSettings onProvidersChanged={probe} refreshToken={providersVersion} />
        </div>
      )}
    </WelcomeStepFrame>
  );
}
