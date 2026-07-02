/**
 * @file_name: SetupPage.tsx
 * @author: NexusAgent
 * @date: 2026-04-02
 * @description: First-time provider configuration page
 *
 * Shown after login when no LLM providers are configured yet (local
 * mode). The primary surface is the shared OneKeyOnboard card: pick a
 * provider (NetMind / Claude / OpenAI / Yunwu / OpenRouter), paste one
 * key, and everything (agent framework, provider, both slots) is wired
 * in one call. The full ProviderSettings stays available behind the
 * "Advanced setup" disclosure; configuring there enables the
 * "Get Started" footer button (re-probed when the disclosure toggles).
 */

import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, ChevronDown, ChevronRight, SkipForward } from 'lucide-react';
import { Button, ScrollArea } from '@/components/ui';
import { BracketSectionLabel } from '@/components/nm';
import { OneKeyOnboard } from '@/components/settings/OneKeyOnboard';
import { ProviderSettings } from '@/components/settings/ProviderSettings';
import { useTheme } from '@/hooks';
import { api } from '@/lib/api';

export function SetupPage() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { isDark } = useTheme();
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [providerCount, setProviderCount] = useState(0);

  // Funnel: user reached the setup page. React StrictMode double-invokes
  // effects in dev, so a ref guard ensures setup_entered fires exactly once
  // per mount. Fire-and-forget.
  const enteredFired = useRef(false);
  useEffect(() => {
    if (enteredFired.current) return;
    enteredFired.current = true;
    api.trackFunnelEvent('setup_entered').catch(() => {});
  }, []);

  // Check current provider count on mount and after changes. Routed
  // through api.getProviders so identity travels in the X-User-Id /
  // JWT header — bare fetch used to send neither, and the backend
  // happily fell back to "first user in users table".
  const probe = async () => {
    try {
      const data = await api.getProviders();
      if (data.success && data.data?.providers) {
        setProviderCount(Object.keys(data.data.providers).length);
      }
    } catch {
      // Backend not ready — keep the skip affordance
    }
  };

  useEffect(() => {
    probe();
  }, []);

  // Funnel: which event fires depends on WHICH button the user pressed, not
  // on provider count — "Skip for now" is a skip; the primary "Get Started"
  // button and one-key onboarding completion are completions. Fire-and-forget.
  const finishSetup = (event: 'setup_completed' | 'setup_skipped') => {
    api.trackFunnelEvent(event).catch(() => {});
    navigate('/app/chat', { replace: true });
  };

  const toggleAdvanced = () => {
    setShowAdvanced((v) => {
      // Re-probe when collapsing — the user may have configured
      // providers inside Advanced, which enables "Get Started".
      if (v) probe();
      return !v;
    });
  };

  return (
    <div className="h-screen w-screen flex flex-col bg-[var(--bg-deep)]">
      {/* Header — original logo preserved */}
      <div className="flex flex-col items-center pt-10 pb-6 animate-fade-in gap-3">
        <img
          src={isDark ? '/logo-dark-mode.svg' : '/logo-light-mode.svg'}
          alt="NarraNexus"
          className="h-14 w-auto object-contain"
        />
        <BracketSectionLabel>Setup · One Key to Start</BracketSectionLabel>
        <h1
          className="text-2xl font-bold"
          style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-display)' }}
        >
          Welcome to NarraNexus
        </h1>
      </div>

      <ScrollArea className="flex-1">
        <div className="max-w-2xl mx-auto px-4 pb-8 animate-fade-in" style={{ animationDelay: '0.05s' }}>
          {/* Primary: one-key onboarding (shared with Settings) */}
          <OneKeyOnboard onComplete={() => finishSetup('setup_completed')} />

          {/* Advanced: the full provider configuration surface */}
          <div className="mt-6">
            <button
              type="button"
              className="flex items-center gap-1.5 mx-auto text-sm hover:opacity-80"
              style={{ color: 'var(--nm-ink70)' }}
              onClick={toggleAdvanced}
            >
              {showAdvanced ? (
                <ChevronDown className="w-4 h-4" />
              ) : (
                <ChevronRight className="w-4 h-4" />
              )}
              Advanced setup — OAuth, custom endpoints, per-slot models
            </button>
            {showAdvanced && (
              <div className="mt-4">
                <ProviderSettings />
              </div>
            )}
          </div>
        </div>
      </ScrollArea>

      {/* Footer actions */}
      <div className="flex items-center justify-center gap-4 py-6 border-t border-[var(--border-default)]">
        {providerCount > 0 ? (
          <Button variant="accent" onClick={() => finishSetup('setup_completed')}>
            Get Started
            <ArrowRight className="w-4 h-4 ml-1" />
          </Button>
        ) : (
          <Button variant="ghost" onClick={() => finishSetup('setup_skipped')}>
            <SkipForward className="w-4 h-4 mr-1" />
            {t('pages.setup.skipForNow')}
          </Button>
        )}
      </div>
    </div>
  );
}

export default SetupPage;
