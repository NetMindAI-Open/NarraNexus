/**
 * PluginsSettings — Settings › Plugins panel (local-only installer for
 * Claude Code / Codex CLI).
 *
 * The desktop app no longer bundles these SDKs (lightweight-plugins
 * cutover — see backend/integrations/plugins) — a user turns a
 * coding-agent framework on by installing its plugin HERE. The two
 * framework pickers (ModelDefaultsSettings / AgentLlmConfigPanel) point
 * their "plugin not installed" notice at this panel (Settings nav id
 * `plugins`), so an uninstalled framework is disabled-but-visible there
 * and actionable here.
 *
 * The install log is rendered inline (not a modal/toast) because a
 * multi-minute pip/npm install with no visible progress reads as a hang —
 * every ndjson line from `api.installPlugin` is appended live.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Download, RefreshCw, Trash2 } from 'lucide-react';
import { api } from '@/lib/api';
import { PaperCard, Button, Spinner, StatusBadge } from '@/components/nm';
import type { PluginStatus, PluginId, PluginInstallEvent } from '@/types';

interface InstallLog {
  lines: string[];
  error: string | null;
}

const MAX_LOG_LINES = 20;

export function PluginsSettings() {
  const { t } = useTranslation();
  const [plugins, setPlugins] = useState<PluginStatus[]>([]);
  const [cloudManaged, setCloudManaged] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [busyIds, setBusyIds] = useState<Set<PluginId>>(new Set());
  const [logs, setLogs] = useState<Record<string, InstallLog>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    try {
      const res = await api.getPlugins();
      if (res.success && res.data) {
        setPlugins(res.data.plugins);
        setCloudManaged(res.data.cloud_managed);
      }
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : t('pages.settings.plugins.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { void load(); }, [load]);

  const setBusy = (id: PluginId, busy: boolean) => {
    setBusyIds((s) => {
      const next = new Set(s);
      if (busy) next.add(id); else next.delete(id);
      return next;
    });
  };

  // Install and update are the SAME call — "update" is just a re-install
  // against the target version the backend already resolved.
  const runInstall = async (id: PluginId) => {
    setBusy(id, true);
    setLogs((s) => ({ ...s, [id]: { lines: [], error: null } }));
    try {
      const final = await api.installPlugin(id, (event: PluginInstallEvent) => {
        if (event.done) return;
        setLogs((s) => ({
          ...s,
          [id]: { lines: [...(s[id]?.lines ?? []), event.line].slice(-MAX_LOG_LINES), error: null },
        }));
      });
      if (!final.ok) {
        setLogs((s) => ({
          ...s,
          [id]: { lines: s[id]?.lines ?? [], error: final.error || t('pages.settings.plugins.installFailed') },
        }));
      }
      await load();
    } catch (e) {
      setLogs((s) => ({
        ...s,
        [id]: {
          lines: s[id]?.lines ?? [],
          error: e instanceof Error ? e.message : t('pages.settings.plugins.installFailed'),
        },
      }));
    } finally {
      setBusy(id, false);
    }
  };

  const runUninstall = async (id: PluginId) => {
    setBusy(id, true);
    try {
      await api.uninstallPlugin(id);
      await load();
    } catch (e) {
      setLogs((s) => ({
        ...s,
        [id]: { lines: [], error: e instanceof Error ? e.message : t('pages.settings.plugins.uninstallFailed') },
      }));
    } finally {
      setBusy(id, false);
    }
  };

  if (loading) {
    return (
      <p className="text-sm text-[var(--text-tertiary)]">{t('pages.settings.plugins.loading')}</p>
    );
  }

  // Cloud installs and manages these plugins centrally — a local install
  // button here would be meaningless (and the install endpoint 403s), so
  // the panel is hidden entirely rather than shown disabled.
  if (cloudManaged) return null;

  return (
    <div className="space-y-4">
      {loadError && <p className="text-sm text-[var(--color-error)]">{loadError}</p>}
      {plugins.map((p) => {
        const isBusy = busyIds.has(p.id) || p.busy;
        const log = logs[p.id];
        return (
          <PaperCard key={p.id} padding="md" className="space-y-3">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-sm font-medium text-[var(--nm-ink)]">{p.display_name}</div>
                <div className="text-xs text-[var(--nm-ink50)] mt-0.5">{p.size_hint}</div>
              </div>
              <div className="flex items-center gap-2">
                {p.installed && (
                  <StatusBadge
                    status={p.update_available || p.version === null ? 'warning' : 'success'}
                  >
                    {p.update_available
                      ? t('pages.settings.plugins.updateAvailable', 'update available')
                      : p.version !== null
                        ? `v${p.version}`
                        : t('pages.settings.plugins.versionUnknown', 'version unknown')}
                  </StatusBadge>
                )}
                {p.installed && p.logged_in && (
                  <StatusBadge status="info">
                    {t('pages.settings.plugins.loggedIn', 'logged in')}
                  </StatusBadge>
                )}
              </div>
            </div>

            {isBusy && (
              <div
                data-testid={`plugin-install-log-${p.id}`}
                className="rounded-[var(--radius-sm)] bg-[var(--nm-paper-warm)] p-2 text-[10px] font-mono text-[var(--nm-ink50)] max-h-24 overflow-y-auto space-y-0.5"
              >
                {(log?.lines ?? []).length === 0 ? (
                  <div className="flex items-center gap-2">
                    <Spinner size={12} />
                    {t('pages.settings.plugins.starting', 'Starting…')}
                  </div>
                ) : (
                  log!.lines.map((line, i) => <div key={i}>{line}</div>)
                )}
              </div>
            )}
            {!isBusy && log?.error && (
              <p className="text-xs text-[var(--color-error)]">{log.error}</p>
            )}

            <div className="flex items-center gap-2">
              {!p.installed && (
                <Button
                  size="sm"
                  leading={<Download className="h-3.5 w-3.5" />}
                  disabled={isBusy}
                  loading={isBusy}
                  onClick={() => void runInstall(p.id)}
                >
                  {t('pages.settings.plugins.install', 'Install')}
                </Button>
              )}
              {p.installed && (p.update_available || p.version === null) && (
                <Button
                  size="sm"
                  variant="secondary"
                  leading={<RefreshCw className="h-3.5 w-3.5" />}
                  disabled={isBusy}
                  loading={isBusy}
                  onClick={() => void runInstall(p.id)}
                >
                  {t('pages.settings.plugins.update', 'Update')}
                </Button>
              )}
              {p.installed && (
                <Button
                  size="sm"
                  variant="ghost"
                  leading={<Trash2 className="h-3.5 w-3.5" />}
                  disabled={isBusy}
                  onClick={() => void runUninstall(p.id)}
                >
                  {t('pages.settings.plugins.uninstall', 'Uninstall')}
                </Button>
              )}
            </div>
          </PaperCard>
        );
      })}
    </div>
  );
}
