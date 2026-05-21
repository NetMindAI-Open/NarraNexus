/**
 * @file_name: SetupPage.tsx
 * @author: NexusAgent
 * @date: 2026-04-02
 * @description: First-time provider configuration page
 *
 * Shown after login when no LLM providers are configured yet.
 * Displays only the ProviderSettings component with a "Done" button.
 * Both local and cloud modes use this page.
 */

import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, SkipForward } from 'lucide-react';
import { Button, ScrollArea } from '@/components/ui';
import { BracketSectionLabel } from '@/components/nm';
import { ProviderSettings } from '@/components/settings/ProviderSettings';
import { useTheme } from '@/hooks';
import { useConfigStore } from '@/stores';
import { api } from '@/lib/api';

export function SetupPage() {
  const navigate = useNavigate();
  const { isDark } = useTheme();
  const userId = useConfigStore((s) => s.userId);
  const [providerCount, setProviderCount] = useState(0);
  const [loaded, setLoaded] = useState(false);

  // Check current provider count on mount and after changes. Routed
  // through api.getProviders so identity travels in the X-User-Id /
  // JWT header — bare fetch used to send neither, and the backend
  // happily fell back to "first user in users table".
  useEffect(() => {
    const check = async () => {
      try {
        const data = await api.getProviders();
        if (data.success && data.data?.providers) {
          setProviderCount(Object.keys(data.data.providers).length);
        }
      } catch {
        // Backend not ready
      }
      setLoaded(true);
    };
    check();
  }, [userId]);

  const handleDone = () => {
    navigate('/app/chat', { replace: true });
  };

  if (!loaded) return null;

  return (
    <div className="h-screen w-screen flex flex-col bg-[var(--bg-deep)]">
      {/* Header — original logo preserved */}
      <div className="flex flex-col items-center pt-10 pb-6 animate-fade-in gap-3">
        <img
          src={isDark ? '/logo-dark-mode.svg' : '/logo-light-mode.svg'}
          alt="NarraNexus"
          className="h-14 w-auto object-contain"
        />
        <BracketSectionLabel>Setup · Configure LLM Providers</BracketSectionLabel>
        <h1
          className="text-2xl font-bold"
          style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-display)' }}
        >
          Configure LLM Providers
        </h1>
        <p className="text-sm" style={{ color: 'var(--nm-ink70)' }}>
          Set up your API keys so NarraNexus can connect to language models.
        </p>
      </div>

      {/* Provider Settings */}
      <ScrollArea className="flex-1">
        <div className="max-w-2xl mx-auto px-4 animate-fade-in" style={{ animationDelay: '0.05s' }}>
          <ProviderSettings />
        </div>
      </ScrollArea>

      {/* Footer actions */}
      <div className="flex items-center justify-center gap-4 py-6 border-t border-[var(--border-default)]">
        {providerCount === 0 && (
          <Button variant="ghost" onClick={handleDone}>
            <SkipForward className="w-4 h-4 mr-1" />
            Skip for now
          </Button>
        )}
        <Button variant="accent" onClick={handleDone}>
          {providerCount > 0 ? 'Get Started' : 'Done'}
          <ArrowRight className="w-4 h-4 ml-1" />
        </Button>
      </div>
    </div>
  );
}

export default SetupPage;
