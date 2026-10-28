/**
 * @file_name: BrowserSettings.tsx
 * @date: 2026-09-22
 * @description: Install, observe and cancel the browser runtime from Settings.
 *
 * Agent refusals name this destination when the runtime is missing. Status
 * is checked on every mount; transport failures must never imply absence.
 */
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Download, MonitorPlay, RefreshCw, X } from 'lucide-react';

import { api } from '@/lib/api';
import type { BrowserInstallResult, BrowserManualInstallHelp, BrowserRuntimeMode, BrowserRuntimeSource, BrowserRuntimeStatus } from '@/types/browser';
import { Button } from '@/components/nm/button';
import { ProgressBar, Spinner } from '@/components/nm/feedback';
import { FormField, Select, Toggle } from '@/components/nm/form';
import { useConfigStore } from '@/stores/configStore';
import { useChatStore } from '@/stores/chatStore';
import BrowserScriptPermissions from './BrowserScriptPermissions';
import BrowserManualInstall from './BrowserManualInstall';

export type RuntimeStatus = BrowserRuntimeStatus;

interface Props {
  /** Injectable for tests; defaults to authenticated API methods. */
  fetchStatus?: () => Promise<RuntimeStatus>;
  startInstall?: () => Promise<Pick<BrowserInstallResult, 'ok' | 'error' | 'manual_install'>>;
  cancelInstall?: () => Promise<{ ok: boolean; error?: string | null }>;
  changeSource?: (source: BrowserRuntimeSource) => Promise<RuntimeStatus>;
  changeMode?: (mode: BrowserRuntimeMode) => Promise<RuntimeStatus>;
}

const defaultFetchStatus = () => api.getBrowserRuntime();
const defaultStartInstall = () => api.installBrowserRuntime();
const defaultCancelInstall = () => api.cancelBrowserInstall();
const defaultChangeSource = (source: BrowserRuntimeSource) => api.setBrowserSource(source);
const defaultChangeMode = (mode: BrowserRuntimeMode) => api.setBrowserMode(mode);

export default function BrowserSettings({
  fetchStatus = defaultFetchStatus,
  startInstall = defaultStartInstall,
  cancelInstall = defaultCancelInstall,
  changeSource = defaultChangeSource,
  changeMode = defaultChangeMode,
}: Props) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<RuntimeStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);
  const [installPending, setInstallPending] = useState(false);
  const [preferencePending, setPreferencePending] = useState(false);
  const [cancelState, setCancelState] = useState<'idle' | 'sending' | 'requested'>('idle');
  const [refreshKey, setRefreshKey] = useState(0);
  const [cancelled, setCancelled] = useState(false);
  const [recovery, setRecovery] = useState<BrowserManualInstallHelp | null>(null);
  const installInFlight = useRef(false);
  const preferenceInFlight = useRef(false);
  const cancelInFlight = useRef(false);
  const mounted = useRef(false);
  const revision = useRef(0);
  const lastStatus = useRef<RuntimeStatus | null>(null);
  const installing = status?.state === 'installing' || installPending;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      const requestRevision = revision.current;
      setChecking(true);
      try {
        const next = await fetchStatus();
        if (!active || revision.current !== requestRevision) return;
        lastStatus.current = next;
        setStatus(next);
        if (next.manual_install) setRecovery(next.manual_install);
        setLoadError(null);
        if (next.state !== 'installing' && !installInFlight.current && !cancelInFlight.current) {
          setCancelState('idle');
        }
      } catch (e) {
        if (!active || revision.current !== requestRevision) return;
        // A failed progress request says nothing about whether the install
        // stopped. Preserve its cancel control and keep checking.
        setLoadError(e instanceof Error ? e.message : String(e));
      } finally {
        if (active) {
          setChecking(false);
          if (installInFlight.current || lastStatus.current?.state === 'installing') {
            timer = setTimeout(() => { void load(); }, 1000);
          }
        }
      }
    };
    void load();
    return () => { active = false; clearTimeout(timer); };
  }, [fetchStatus, refreshKey]);

  const refresh = () => {
    revision.current += 1;
    setRefreshKey((n) => n + 1);
  };

  const onPreference = async (save: () => Promise<RuntimeStatus>) => {
    if (preferenceInFlight.current || installing || checking || !status?.selection?.editable) return;
    preferenceInFlight.current = true;
    setPreferencePending(true);
    setError(null);
    revision.current += 1;
    try {
      const next = await save();
      if (!mounted.current) return;
      lastStatus.current = next;
      setStatus(next);
      setLoadError(null);
      if (next.manual_install) setRecovery(next.manual_install);
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      preferenceInFlight.current = false;
      if (mounted.current) setPreferencePending(false);
    }
  };

  const onInstall = async () => {
    if (installInFlight.current || preferenceInFlight.current || installing || checking) return;
    installInFlight.current = true;
    setError(null);
    setCancelled(false);
    setCancelState('idle');
    setInstallPending(true);
    refresh();
    try {
      const out = await startInstall();
      if (!mounted.current) return;
      if (out?.manual_install) setRecovery(out.manual_install);
      if (out?.ok !== true) {
        if (out?.error === 'cancelled') setCancelled(true);
        else setError(out?.error || t('settings.browser.installFailed', 'Could not install the browser. Please try again.'));
      }
    } catch (e) {
      if (mounted.current) setError(e instanceof Error && e.message ? e.message
        : t('settings.browser.installFailed', 'Could not install the browser. Please try again.'));
    } finally {
      installInFlight.current = false;
      if (mounted.current) {
        setInstallPending(false);
        refresh();
      }
    }
  };

  const onCancel = async () => {
    if (cancelInFlight.current || cancelState === 'requested') return;
    cancelInFlight.current = true;
    setCancelState('sending');
    setError(null);
    try {
      const out = await cancelInstall();
      if (!mounted.current) return;
      if (out?.ok !== true) throw new Error(out?.error
        || t('settings.browser.cancelFailed', 'Could not cancel the installation. Please try again.'));
      setCancelled(true);
      setCancelState('requested');
    } catch (e) {
      if (!mounted.current) return;
      setCancelState('idle');
      setError(e instanceof Error && e.message ? e.message
        : t('settings.browser.cancelFailed', 'Could not cancel the installation. Please try again.'));
    } finally {
      cancelInFlight.current = false;
      if (mounted.current) refresh();
    }
  };

  const progress = status?.state === 'installing' ? status.progress : null;
  const percent = progress?.phase === 'downloading' && progress.bytes_total !== null
    && progress.bytes_total > 0 && typeof progress.percent === 'number' && Number.isFinite(progress.percent)
    ? Math.min(100, Math.max(0, progress.percent)) : null;
  const progressLabel = cancelState === 'sending'
    ? t('settings.browser.cancelling', 'Requesting cancellation...')
    : cancelState === 'requested'
      ? t('settings.browser.cancelRequested', 'Cancellation requested. Waiting for installation to stop...')
      : progress?.phase === 'extracting'
        ? t('settings.browser.extracting', 'Extracting browser...')
        : progress?.phase === 'verifying'
          ? t('settings.browser.verifying', 'Verifying browser...')
          : progress?.phase === 'downloading'
            ? t('settings.browser.installingUnknown', 'Downloading...')
            : t('settings.browser.starting', 'Starting installation...');

  return (
    <div className="space-y-4" data-testid="browser-settings">
      <h3 className="text-sm font-medium flex items-center gap-2">
        <MonitorPlay className="w-4 h-4" />
        {t('settings.browser.title', 'Browser')}
      </h3>

      {status?.selection?.editable && <FormField label={t('settings.browser.sourceLabel', 'Browser source')}
        hint={<span data-testid="browser-source-effect">{t('settings.browser.sourceEffect', 'Applies to new browser sessions. Existing sessions keep running. Each browser keeps separate sign-ins.')}</span>}>
        <Select data-testid="browser-runtime-source" value={status.selection.source}
          disabled={preferencePending || installing || checking}
          options={[
            { value: 'managed', label: t('settings.browser.sourceManaged', 'Managed Chrome for Testing') },
            { value: 'system', label: t('settings.browser.sourceSystem', 'Installed Google Chrome') },
          ]}
          onChange={event => {
            const source = event.target.value as BrowserRuntimeSource;
            if (source !== status.selection?.source) void onPreference(() => changeSource(source));
          }} />
      </FormField>}

      {status?.selection?.editable && <div className="space-y-1.5">
        <Toggle checked={status.selection.mode === 'headed'}
          disabled={preferencePending || installing || checking}
          label={t('settings.browser.showWindow', 'Show browser window (headed)')}
          onChange={checked => void onPreference(() => changeMode(checked ? 'headed' : 'headless'))} />
        <p data-testid="browser-mode-effect" className="text-xs text-[var(--text-secondary)] max-w-prose break-words">
          {t('settings.browser.modeEffect', 'Headless mode is the default. Changes apply on the next browser launch; sign-ins are kept. Some websites do not support headless access. If a site reports an unsupported browser or other errors, manually turn on headed mode, restart the browser, and retry.')}
        </p>
      </div>}

      {loadError !== null && (
        <div className="space-y-2" data-testid="browser-settings-load-error">
          <p role="alert" className="max-w-prose break-words text-xs text-[var(--color-error)]">
            {t('settings.browser.statusFailed', 'Could not check browser status.')} {loadError}
          </p>
          <Button variant="secondary" size="sm" disabled={preferencePending} loading={checking} onClick={refresh}
            leading={<RefreshCw className="h-3.5 w-3.5" />} data-testid="browser-settings-retry">
            {t('settings.browser.recheck', 'Check again')}
          </Button>
        </div>
      )}

      {!status && !loadError && (
        <div className="flex items-center gap-2 text-xs text-[var(--text-secondary)]" data-testid="browser-settings-loading">
          <Spinner size={16} label={t('settings.browser.loading', 'Checking browser status...')} />
          <span>{t('settings.browser.loading', 'Checking browser status...')}</span>
        </div>
      )}

      {installing ? (
        <div className="w-full max-w-sm space-y-2" data-testid="browser-settings-progress">
          {percent !== null && cancelState === 'idle' && !loadError ? (
            <ProgressBar value={percent} label={progressLabel} showPercent />
          ) : (
            <div className="flex items-center gap-2 text-xs text-[var(--text-secondary)]">
              <Spinner size={16} label={progressLabel} className="shrink-0" />
              <span>{progressLabel}</span>
            </div>
          )}
          <Button variant="secondary" size="sm" onClick={() => void onCancel()}
            disabled={cancelState !== 'idle'} loading={cancelState === 'sending'}
            leading={<X className="h-3.5 w-3.5" />} data-testid="browser-settings-cancel">
            {t('settings.browser.cancel', 'Cancel installation')}
          </Button>
        </div>
      ) : !loadError && status?.state === 'ready' ? (
        <div className="flex flex-wrap items-center gap-2 text-xs" data-testid="browser-settings-ready">
          <Check className="w-4 h-4 text-[var(--color-success)]" />
          <span>{t('settings.browser.ready', 'Installed')}</span>
          {status.version && <span className="min-w-0 break-words text-[var(--text-secondary)]">{status.version}</span>}
          <Button variant="ghost" size="sm" disabled={preferencePending} loading={checking} onClick={refresh}
            leading={<RefreshCw className="h-3.5 w-3.5" />} data-testid="browser-settings-recheck">
            {t('settings.browser.recheck', 'Check again')}
          </Button>
        </div>
      ) : !loadError && status ? (
        <div className="space-y-2">
          {cancelled && <p role="status" className="text-xs text-[var(--text-secondary)]">
            {t('settings.browser.cancelled', 'Installation cancelled.')}
          </p>}
          <div className="text-xs text-[var(--text-secondary)]" data-testid="browser-settings-state">
            {status.selection?.source === 'system'
              ? t('settings.browser.systemUnavailable', 'Google Chrome is unavailable. Install Google Chrome or select the managed browser.')
              : status.reason === 'probe-failed'
              ? t('settings.browser.broken', 'A browser is installed but will not start.')
              : t('settings.browser.absent', 'Not installed yet.')}
          </div>
          {status.selection?.source !== 'system' && <Button variant="secondary" size="sm" disabled={checking || preferencePending || cancelState !== 'idle'}
            leading={<Download className="h-3.5 w-3.5" />} onClick={() => void onInstall()}
            data-testid="browser-settings-install">
            {status.reason === 'probe-failed'
              ? t('settings.browser.reinstall', 'Re-install browser')
              : t('settings.browser.install', 'Install browser')}
          </Button>}
        </div>
      ) : null}

      {error && (
        <div role="alert" className="text-xs text-[var(--color-error)] max-w-prose break-words" data-testid="browser-settings-error">
          {error}
        </div>
      )}
      {!installing && status?.state !== 'ready' && status?.selection?.source !== 'system' && recovery && <BrowserManualInstall
        command={recovery.command} statusCommand={recovery.status_command} cancelCommand={recovery.cancel_command}
        root={recovery.root} shell={recovery.shell} />}
      <AgentScriptPermissions />
    </div>
  );
}

function AgentScriptPermissions() {
  const { t } = useTranslation();
  const agents = useConfigStore((state) => state.agents);
  const userId = useConfigStore((state) => state.userId);
  const activeAgentId = useChatStore((state) => state.activeAgentId);
  const [chosen, setChosen] = useState<string | null>(null);
  const ownedAgents = agents.filter((agent) => !agent.created_by || agent.created_by === userId);
  const selected = ownedAgents.find((agent) => agent.agent_id === chosen)
    ?? ownedAgents.find((agent) => agent.agent_id === activeAgentId) ?? ownedAgents[0];

  return <div className="min-w-0 space-y-4 border-t border-[var(--nm-hairline)] pt-4">
    <FormField label={t('settings.browser.agentLabel', 'Agent')}>
      <Select value={selected?.agent_id ?? ''} disabled={ownedAgents.length === 0}
        placeholder={t('settings.browser.chooseAgent', 'Select an agent')}
        options={ownedAgents.map((agent) => ({ value: agent.agent_id, label: agent.name || agent.agent_id }))}
        onChange={(event) => setChosen(event.target.value)} />
    </FormField>
    {selected && <BrowserScriptPermissions agentId={selected.agent_id} />}
  </div>;
}
