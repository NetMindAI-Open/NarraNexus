/**
 * @file_name: SettingsPage.tsx
 * @author: NexusAgent
 * @date: 2026-04-02
 * @description: Settings page — reuses existing ProviderSettings + adds mode switching
 *
 * Uses the existing ProviderSettings component (which calls /api/providers)
 * for LLM configuration, and adds a mode switch section for local/cloud toggle.
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { RotateCcw, Monitor, Cloud } from 'lucide-react';
import { Button, Card, CardContent } from '@/components/ui';
import { ProviderSettings } from '@/components/settings/ProviderSettings';
import { EmbeddingStatus } from '@/components/ui/EmbeddingStatus';
import { useRuntimeStore } from '@/stores/runtimeStore';

export default function SettingsPage() {
  const navigate = useNavigate();
  const { mode, setMode } = useRuntimeStore();
  const [showModeConfirm, setShowModeConfirm] = useState(false);

  const handleSwitchMode = () => {
    // Reset mode — go back to mode selection
    setMode(null);
    navigate('/mode-select');
  };

  return (
    <div className="h-full overflow-y-auto p-6 space-y-6">
      {/* LLM Provider Configuration — uses existing component that calls /api/providers */}
      <section>
        <h2 className="text-lg font-semibold mb-4" style={{ color: 'var(--text-primary)' }}>
          LLM Providers
        </h2>
        <ProviderSettings />
      </section>

      {/* Embedding Status */}
      <section>
        <h2 className="text-lg font-semibold mb-4" style={{ color: 'var(--text-primary)' }}>
          Embedding Index
        </h2>
        <EmbeddingStatus />
      </section>

      {/* Mode Switching */}
      <section>
        <h2 className="text-lg font-semibold mb-4" style={{ color: 'var(--text-primary)' }}>
          Runtime Mode
        </h2>
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                {mode === 'local' ? (
                  <Monitor className="w-5 h-5" style={{ color: 'var(--accent-primary)' }} />
                ) : (
                  <Cloud className="w-5 h-5" style={{ color: 'var(--accent-primary)' }} />
                )}
                <div>
                  <p className="font-medium" style={{ color: 'var(--text-primary)' }}>
                    {mode === 'local' ? 'Local Mode' : 'Cloud Mode'}
                  </p>
                  <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
                    {mode === 'local'
                      ? 'Everything runs on your machine. Data stays local.'
                      : 'Connected to cloud services.'}
                  </p>
                </div>
              </div>

              {!showModeConfirm ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setShowModeConfirm(true)}
                >
                  <RotateCcw className="w-4 h-4 mr-1" />
                  Switch Mode
                </Button>
              ) : (
                <div className="flex items-center gap-2">
                  <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>
                    Switch to {mode === 'local' ? 'Cloud' : 'Local'}?
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setShowModeConfirm(false)}
                  >
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    onClick={handleSwitchMode}
                  >
                    Confirm
                  </Button>
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
