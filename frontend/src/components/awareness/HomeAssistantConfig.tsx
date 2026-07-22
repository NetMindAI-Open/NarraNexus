/**
 * HomeAssistantConfig — per-agent Home Assistant binding.
 *
 * Lets the user connect an agent to their Home Assistant (base URL +
 * Long-Lived Access Token) so the agent can query/control smart-home devices
 * via the HomeAssistantModule. Per-agent by design: a user with multiple HA
 * instances (home vs. office) can bind different agents to different HAs. Not
 * an IM channel — its own capability section in the right-side panel.
 *
 * States:
 *   1. Not bound → form (base URL, token, verify TLS) + Test + Save
 *   2. Bound → show base URL + masked token + Test + Re-bind (edit)
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Home, Loader2, CheckCircle, AlertCircle, Link as LinkIcon, Copy } from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent, Button, Input } from '@/components/ui';
import { useConfigStore } from '@/stores';
import { api } from '@/lib/api';

export function HomeAssistantConfig() {
  const { t } = useTranslation();
  const { agentId } = useConfigStore();
  const mountedRef = useRef(true);

  const [loading, setLoading] = useState(false);
  const [bound, setBound] = useState(false);
  const [boundUrl, setBoundUrl] = useState('');
  const [tokenMasked, setTokenMasked] = useState('');
  const [editing, setEditing] = useState(false);

  // Form state
  const [baseUrl, setBaseUrl] = useState('');
  const [token, setToken] = useState('');
  const [verifyTls, setVerifyTls] = useState(true);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [testResult, setTestResult] = useState<{ ok: boolean; count?: number; msg?: string } | null>(null);
  const [promptCopied, setPromptCopied] = useState(false);

  const handleCopyPrompt = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(t('awareness.homeAssistant.setupPrompt'));
      setPromptCopied(true);
      setTimeout(() => setPromptCopied(false), 2000);
    } catch {
      /* Clipboard denied — silently ignore; the prompt text is still visible to copy manually. */
    }
  }, [t]);

  const fetchBinding = useCallback(async () => {
    if (!agentId) return;
    setLoading(true);
    setError('');
    try {
      const res = await api.getHABinding(agentId);
      if (!mountedRef.current) return;
      setBound(!!res.bound);
      setBoundUrl(res.base_url || '');
      setTokenMasked(res.token_masked || '');
      setEditing(!res.bound);
    } catch (e: unknown) {
      if (mountedRef.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    mountedRef.current = true;
    setBaseUrl('');
    setToken('');
    setVerifyTls(true);
    setTestResult(null);
    fetchBinding();
    return () => {
      mountedRef.current = false;
    };
  }, [fetchBinding]);

  const handleTest = async () => {
    if (!baseUrl || !token) return;
    setBusy(true);
    setTestResult(null);
    setError('');
    try {
      const res = await api.testHAConnection(baseUrl, token, verifyTls);
      if (!mountedRef.current) return;
      setTestResult(res.ok ? { ok: true, count: res.entity_count } : { ok: false, msg: res.error });
    } catch (e: unknown) {
      if (mountedRef.current) setTestResult({ ok: false, msg: e instanceof Error ? e.message : String(e) });
    } finally {
      if (mountedRef.current) setBusy(false);
    }
  };

  const handleVerify = async () => {
    if (!agentId) return;
    setBusy(true);
    setTestResult(null);
    setError('');
    try {
      const res = await api.verifyHABinding(agentId);
      if (!mountedRef.current) return;
      setTestResult(res.ok ? { ok: true, count: res.entity_count } : { ok: false, msg: res.error });
    } catch (e: unknown) {
      if (mountedRef.current) setTestResult({ ok: false, msg: e instanceof Error ? e.message : String(e) });
    } finally {
      if (mountedRef.current) setBusy(false);
    }
  };

  const handleSave = async () => {
    if (!agentId || !baseUrl || !token) return;
    setBusy(true);
    setError('');
    try {
      const res = await api.saveHABinding(agentId, baseUrl, token, verifyTls);
      if (!mountedRef.current) return;
      if (res.ok) {
        setToken('');
        setEditing(false);
        await fetchBinding();
      } else {
        setError(t('awareness.homeAssistant.errSave'));
      }
    } catch (e: unknown) {
      if (mountedRef.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (mountedRef.current) setBusy(false);
    }
  };

  const title = (
    <CardTitle className="flex items-center gap-2">
      <Home className="w-4 h-4" />
      {t('awareness.homeAssistant.title')}
    </CardTitle>
  );

  if (loading) {
    return (
      <Card>
        <CardHeader>{title}</CardHeader>
        <CardContent>
          <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
            <Loader2 className="w-4 h-4 animate-spin" /> {t('awareness.common.loading')}
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>{title}</CardHeader>
      <CardContent className="space-y-3">
        {error && (
          <div role="alert" className="flex items-center gap-2 text-sm text-[var(--color-red-500)] border border-[var(--color-red-500)] p-2">
            <AlertCircle className="w-4 h-4 flex-shrink-0" aria-hidden="true" />
            {error}
          </div>
        )}

        {/* Bound summary (not editing) */}
        {bound && !editing ? (
          <div className="space-y-2">
            <div className="flex items-center gap-1.5 text-xs text-[var(--color-green-500)]">
              <CheckCircle className="w-3 h-3" aria-hidden="true" /> {t('awareness.homeAssistant.connected')}
            </div>
            <div className="text-xs text-[var(--text-secondary)] break-all">{boundUrl}</div>
            <div className="text-xs text-[var(--text-tertiary)] font-mono">{tokenMasked}</div>

            {testResult && (
              <div
                className={`text-xs flex items-center gap-1.5 ${testResult.ok ? 'text-[var(--color-green-500)]' : 'text-[var(--color-red-500)]'}`}
              >
                {testResult.ok ? <CheckCircle className="w-3 h-3" /> : <AlertCircle className="w-3 h-3" />}
                {testResult.ok
                  ? t('awareness.homeAssistant.testOk', { count: testResult.count ?? 0 })
                  : testResult.msg || t('awareness.homeAssistant.testFail')}
              </div>
            )}

            <div className="flex gap-2">
              <Button onClick={handleVerify} disabled={busy} variant="ghost" size="sm" className="flex-1">
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : t('awareness.homeAssistant.verify')}
              </Button>
              <Button onClick={() => setEditing(true)} variant="ghost" size="sm" className="flex-1">
                <LinkIcon className="w-4 h-4 mr-2" /> {t('awareness.homeAssistant.rebind')}
              </Button>
            </div>
          </div>
        ) : (
          /* Bind / edit form */
          <div className="space-y-2">
            <p className="text-xs text-[var(--text-secondary)]">{t('awareness.homeAssistant.intro')}</p>
            <Input
              placeholder={t('awareness.homeAssistant.baseUrlPlaceholder')}
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              className="text-sm"
              aria-label={t('awareness.homeAssistant.baseUrl')}
            />
            <Input
              type="password"
              placeholder={t('awareness.homeAssistant.tokenPlaceholder')}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              className="text-sm"
              aria-label={t('awareness.homeAssistant.token')}
            />
            <label className="flex items-center gap-2 text-xs text-[var(--text-secondary)]">
              <input type="checkbox" checked={verifyTls} onChange={(e) => setVerifyTls(e.target.checked)} />
              {t('awareness.homeAssistant.verifyTls')}
            </label>

            {testResult && (
              <div
                className={`text-xs flex items-center gap-1.5 ${testResult.ok ? 'text-[var(--color-green-500)]' : 'text-[var(--color-red-500)]'}`}
              >
                {testResult.ok ? <CheckCircle className="w-3 h-3" /> : <AlertCircle className="w-3 h-3" />}
                {testResult.ok
                  ? t('awareness.homeAssistant.testOk', { count: testResult.count ?? 0 })
                  : testResult.msg || t('awareness.homeAssistant.testFail')}
              </div>
            )}

            <div className="flex gap-2">
              <Button onClick={handleTest} disabled={busy || !baseUrl || !token} variant="ghost" size="sm" className="flex-1">
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : t('awareness.homeAssistant.test')}
              </Button>
              <Button onClick={handleSave} disabled={busy || !baseUrl || !token} size="sm" className="flex-1">
                {t('awareness.common.save')}
              </Button>
            </div>

            {/* Setup guide: copy a prompt that has the agent deploy HA + connect devices,
                for users who don't have a Home Assistant instance yet. */}
            <div className="mt-1 pt-3 border-t border-[var(--border-default)] space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-[var(--text-secondary)]">
                  {t('awareness.homeAssistant.setupGuideTitle')}
                </span>
                <button
                  type="button"
                  onClick={handleCopyPrompt}
                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-[var(--border-default)] hover:bg-[var(--bg-tertiary)] transition-colors text-[var(--text-secondary)] hover:text-[var(--text-primary)] text-xs"
                >
                  {promptCopied ? (
                    <>
                      <CheckCircle className="w-3 h-3 text-[var(--color-green-500)]" />
                      <span>{t('awareness.common.copied')}</span>
                    </>
                  ) : (
                    <>
                      <Copy className="w-3 h-3" />
                      <span>{t('awareness.homeAssistant.copyPrompt')}</span>
                    </>
                  )}
                </button>
              </div>
              <p className="text-[11px] leading-relaxed text-[var(--text-tertiary)]">
                {t('awareness.homeAssistant.setupGuideHint')}
              </p>
              <pre className="max-h-32 overflow-y-auto rounded border border-[var(--border-default)] bg-[var(--bg-tertiary)] p-2 text-[10px] font-mono leading-relaxed whitespace-pre-wrap text-[var(--text-secondary)]">
                {t('awareness.homeAssistant.setupPrompt')}
              </pre>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
