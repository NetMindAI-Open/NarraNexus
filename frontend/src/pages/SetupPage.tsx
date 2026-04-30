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
import { Settings, ArrowRight, SkipForward } from 'lucide-react';
import { Button, ScrollArea } from '@/components/ui';
import { ProviderSettings } from '@/components/settings/ProviderSettings';
import { useConfigStore } from '@/stores';
import { getBaseUrl } from '@/lib/api';

export function SetupPage() {
  const navigate = useNavigate();
  const userId = useConfigStore((s) => s.userId);
  const [providerCount, setProviderCount] = useState(0);
  const [loaded, setLoaded] = useState(false);

  // Check current provider count on mount and after changes
  useEffect(() => {
    const check = async () => {
      try {
        const baseUrl = getBaseUrl();
        const res = await fetch(`${baseUrl}/api/providers?user_id=${encodeURIComponent(userId)}`);
        const data = await res.json();
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
      {/* Header */}
      <div className="flex flex-col items-center pt-10 pb-6 animate-fade-in">
        <div className="w-12 h-12 rounded-2xl bg-[var(--gradient-primary)] flex items-center justify-center shadow-[0_0_20px_var(--accent-glow)] mb-4">
          <Settings className="w-6 h-6 text-[var(--text-inverse)] dark:text-[var(--bg-deep)]" />
        </div>
        <h1 className="text-2xl font-bold text-[var(--text-primary)]">
          Configure LLM Providers
        </h1>
        <p className="text-sm text-[var(--text-secondary)] mt-2">
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
