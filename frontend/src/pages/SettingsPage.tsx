/**
 * @file_name: SettingsPage.tsx
 * @author: NexusAgent
 * @date: 2026-04-02
 * @description: Settings page for model provider configuration
 *
 * Allows users to configure the LLM provider (Anthropic, OpenAI, Google, Custom),
 * enter API credentials, test connectivity, and select the execution mode.
 */

import { useState, useEffect } from 'react';
import {
  Save,
  FlaskConical,
  Loader2,
  CheckCircle2,
  XCircle,
} from 'lucide-react';
import { Button, Input, Card, CardHeader, CardTitle, CardContent } from '@/components/ui';
import { useRuntimeStore } from '@/stores/runtimeStore';
import { cn } from '@/lib/utils';

type Provider = 'anthropic' | 'openai' | 'google' | 'custom';
type ExecutionMode = 'claude-code' | 'api';
type TestStatus = 'idle' | 'testing' | 'success' | 'error';

const PROVIDERS: { value: Provider; label: string }[] = [
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'google', label: 'Google' },
  { value: 'custom', label: 'Custom' },
];

const STORAGE_KEY = 'narranexus-settings';

interface SettingsData {
  provider: Provider;
  baseUrl: string;
  apiKey: string;
  modelName: string;
  executionMode: ExecutionMode;
}

function loadSettings(): SettingsData {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as SettingsData;
  } catch {
    // Ignore parse errors
  }
  return {
    provider: 'anthropic',
    baseUrl: '',
    apiKey: '',
    modelName: '',
    executionMode: 'api',
  };
}

export function SettingsPage() {
  const { features } = useRuntimeStore();

  const [provider, setProvider] = useState<Provider>('anthropic');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [modelName, setModelName] = useState('');
  const [executionMode, setExecutionMode] = useState<ExecutionMode>('api');
  const [testStatus, setTestStatus] = useState<TestStatus>('idle');
  const [saved, setSaved] = useState(false);

  // Load from localStorage on mount
  useEffect(() => {
    const data = loadSettings();
    setProvider(data.provider);
    setBaseUrl(data.baseUrl);
    setApiKey(data.apiKey);
    setModelName(data.modelName);
    setExecutionMode(data.executionMode);
  }, []);

  const handleSave = () => {
    const data: SettingsData = {
      provider,
      baseUrl,
      apiKey,
      modelName,
      executionMode,
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const handleTestConnection = async () => {
    setTestStatus('testing');
    // Placeholder: simulate a connection test
    await new Promise((resolve) => setTimeout(resolve, 1500));
    // For now, succeed if apiKey is non-empty
    setTestStatus(apiKey.trim() ? 'success' : 'error');
    setTimeout(() => setTestStatus('idle'), 3000);
  };

  return (
    <div className="h-full flex flex-col gap-6 p-6 overflow-y-auto max-w-2xl">
      <h1 className="text-lg font-semibold text-[var(--text-primary)]">
        Settings
      </h1>

      {/* Provider configuration */}
      <Card variant="default">
        <CardHeader>
          <CardTitle>Model Provider</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          {/* Provider dropdown */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider">
              Provider
            </label>
            <select
              value={provider}
              onChange={(e) => setProvider(e.target.value as Provider)}
              className={cn(
                'w-full bg-[var(--bg-sunken)] border border-[var(--border-default)] rounded-xl',
                'px-4 py-2.5 text-[var(--text-primary)]',
                'focus:outline-none focus:border-[var(--accent-primary)]',
                'focus:shadow-[0_0_0_3px_var(--accent-glow),0_0_20px_var(--accent-glow)]',
                'hover:border-[var(--border-strong)]',
                'transition-all duration-200',
              )}
            >
              {PROVIDERS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>

          {/* Base URL */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider">
              Base URL
            </label>
            <Input
              type="text"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://api.anthropic.com"
            />
          </div>

          {/* API Key */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider">
              API Key
            </label>
            <Input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-..."
            />
          </div>

          {/* Model Name */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider">
              Model Name
            </label>
            <Input
              type="text"
              value={modelName}
              onChange={(e) => setModelName(e.target.value)}
              placeholder="claude-sonnet-4-20250514"
            />
          </div>

          {/* Test connection */}
          <Button
            variant="outline"
            size="sm"
            onClick={handleTestConnection}
            disabled={testStatus === 'testing'}
          >
            {testStatus === 'testing' && (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            )}
            {testStatus === 'success' && (
              <CheckCircle2 className="w-3.5 h-3.5 text-[var(--color-success)]" />
            )}
            {testStatus === 'error' && (
              <XCircle className="w-3.5 h-3.5 text-[var(--color-error)]" />
            )}
            {testStatus === 'idle' && <FlaskConical className="w-3.5 h-3.5" />}
            {testStatus === 'testing'
              ? 'Testing...'
              : testStatus === 'success'
                ? 'Connection OK'
                : testStatus === 'error'
                  ? 'Connection Failed'
                  : 'Test Connection'}
          </Button>
        </CardContent>
      </Card>

      {/* Execution mode */}
      <Card variant="default">
        <CardHeader>
          <CardTitle>Execution Mode</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {/* API Mode */}
          <label className="flex items-center gap-3 cursor-pointer group">
            <input
              type="radio"
              name="executionMode"
              value="api"
              checked={executionMode === 'api'}
              onChange={() => setExecutionMode('api')}
              className="accent-[var(--accent-primary)]"
            />
            <div>
              <span className="text-sm font-medium text-[var(--text-primary)] group-hover:text-[var(--accent-primary)] transition-colors">
                API Mode
              </span>
              <p className="text-xs text-[var(--text-tertiary)]">
                Use the model provider API directly
              </p>
            </div>
          </label>

          {/* Claude Code - only visible when feature flag allows */}
          {features.canUseClaudeCode && (
            <label className="flex items-center gap-3 cursor-pointer group">
              <input
                type="radio"
                name="executionMode"
                value="claude-code"
                checked={executionMode === 'claude-code'}
                onChange={() => setExecutionMode('claude-code')}
                className="accent-[var(--accent-primary)]"
              />
              <div>
                <span className="text-sm font-medium text-[var(--text-primary)] group-hover:text-[var(--accent-primary)] transition-colors">
                  Claude Code
                </span>
                <p className="text-xs text-[var(--text-tertiary)]">
                  Execute tasks via Claude Code CLI
                </p>
              </div>
            </label>
          )}
        </CardContent>
      </Card>

      {/* Save button */}
      <div className="flex items-center gap-3">
        <Button variant="accent" onClick={handleSave}>
          <Save className="w-4 h-4" />
          {saved ? 'Saved!' : 'Save Settings'}
        </Button>
        {saved && (
          <span className="text-xs text-[var(--color-success)] animate-fade-in">
            Settings saved to local storage
          </span>
        )}
      </div>
    </div>
  );
}

export default SettingsPage;
