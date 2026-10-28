/**
 * @file_name: BrowserScriptPermissions.tsx
 * @description: Explicit advanced-script settings, independent of unrestricted browsing.
 */
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, RefreshCw, Save } from 'lucide-react';
import type { BrowserPolicyView } from '@/types/browser';
import { Button } from '@/components/nm/button';
import { Checkbox, FormField, TextInput } from '@/components/nm/form';
import { Spinner } from '@/components/nm/feedback';
import { api } from '@/lib/api';

interface Actions {
  load: (agentId: string) => Promise<BrowserPolicyView>;
  saveScripts: (agentId: string, origin: string, allowed: boolean) => Promise<{ ok: boolean }>;
}

type Props = { agentId: string } & Partial<Actions>;

function isPolicyFor(view: BrowserPolicyView, agentId: string): boolean {
  const verdicts = ['allow', 'ask', 'deny'];
  return view?.agent_id === agentId && Array.isArray(view.origins)
    && verdicts.includes(view.defaults?.full_cdp_access)
    && view.origins.every((site) => typeof site.origin === 'string' && verdicts.includes(site.full_cdp_access));
}

const loadPermissions: Actions['load'] = (agentId) => api.getBrowserPolicy(agentId);
const updateScripts: Actions['saveScripts'] = async (agentId, origin, allowed) => ({
  ok: isPolicyFor(await api.updateBrowserPolicy(agentId, {
    origin, capability: 'full_cdp_access', verdict: allowed ? 'allow' : 'deny',
  }), agentId),
});

export default function BrowserScriptPermissions({ agentId, load = loadPermissions, saveScripts = updateScripts }: Props) {
  return <AgentPermissions key={agentId} agentId={agentId} load={load} saveScripts={saveScripts} />;
}

function AgentPermissions({ agentId, load, saveScripts }: { agentId: string } & Actions) {
  const { t } = useTranslation();
  const [policy, setPolicy] = useState<BrowserPolicyView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);
  const [revision, setRevision] = useState(0);
  const [origin, setOrigin] = useState('');
  const [draftOrigin, setDraftOrigin] = useState<string | null>(null);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      setChecking(true);
      try {
        const result = await load(agentId);
        if (!active) return;
        if (!isPolicyFor(result, agentId)) throw new Error(t('settings.browser.scriptsFailed', 'Could not load script permissions.'));
        setPolicy(result);
        setError(null);
      } catch (cause) {
        if (!active) return;
        setPolicy(null);
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        if (active) setChecking(false);
      }
    };
    void refresh();
    return () => { active = false; };
  }, [agentId, load, revision, t]);

  const saved = () => {
    if (mounted.current) setRevision((value) => value + 1);
  };
  const sites = policy?.origins ?? [];
  const addSite = () => {
    try {
      const url = new URL(origin.trim());
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password
          || url.pathname !== '/' || url.search || url.hash) throw new Error('Invalid origin');
      setDraftOrigin(url.origin);
      setOrigin('');
      setError(null);
    } catch {
      setError(t('settings.browser.scriptOriginInvalid', 'Enter an HTTP or HTTPS origin without a path or credentials.'));
    }
  };

  return <section className="min-w-0 space-y-3" aria-label={t('settings.browser.scriptsTitle', 'Advanced scripts')}>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-sm font-medium">{t('settings.browser.scriptsTitle', 'Advanced scripts')}</h3>
      <Button size="sm" variant="ghost" loading={checking}
        leading={<RefreshCw className="h-3.5 w-3.5" />}
        onClick={() => setRevision((value) => value + 1)}>
        {t('settings.browser.refreshScripts', 'Refresh')}
      </Button>
    </div>
    {error !== null && <p role="alert" className="break-words text-xs text-[var(--color-error)]">{error}</p>}
    {checking && policy === null && <Spinner size={16} label={t('settings.browser.scriptsLoading', 'Loading script permissions...')} />}
    {policy && <>
      <div className="flex min-w-0 flex-wrap items-end gap-2">
        <FormField label={t('settings.browser.scriptOrigin', 'Site origin')} className="min-w-0 flex-1">
          <TextInput value={origin} onChange={(event) => setOrigin(event.target.value)}
            disabled={checking} placeholder="https://example.com" />
        </FormField>
          <Button size="sm" variant="secondary" disabled={checking || !origin.trim()}
            leading={<Plus className="h-3.5 w-3.5" />} onClick={addSite}>
            {t('settings.browser.addScriptSite', 'Add site')}
          </Button>
      </div>
      {sites.map((site) => <ScriptRow key={site.origin} origin={site.origin} allowed={site.full_cdp_access === 'allow'}
        agentId={agentId} checking={checking} saveScripts={saveScripts} onSaved={saved} />)}
      {draftOrigin && !sites.some((site) => site.origin === draftOrigin) && <ScriptRow key={draftOrigin}
        origin={draftOrigin} allowed={policy.defaults.full_cdp_access === 'allow'} agentId={agentId}
        checking={checking} saveScripts={saveScripts} onSaved={saved} />}
    </>}
  </section>;
}

function ScriptRow({ origin, allowed, agentId, checking, saveScripts, onSaved }: {
  origin: string; allowed: boolean; agentId: string; checking: boolean;
  saveScripts: Actions['saveScripts']; onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<boolean | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  const scripts = draft ?? allowed;
  const save = async () => {
    if (inFlight.current || checking) return;
    inFlight.current = true;
    setPending(true);
    setError(null);
    try {
      const result = await saveScripts(agentId, origin, scripts);
      if (result?.ok !== true) throw new Error(t('settings.browser.permissionSaveFailed', 'Could not save this permission. Please try again.'));
      setDraft(null);
      onSaved();
    } catch (cause) {
      setError(cause instanceof Error && cause.message ? cause.message
        : t('settings.browser.permissionSaveFailed', 'Could not save this permission. Please try again.'));
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  };

  return <div className="min-w-0 space-y-2 border-t border-[var(--nm-hairline)] py-3">
    <p className="break-all text-sm font-medium">{origin}</p>
    <Checkbox checked={scripts} disabled={checking || pending}
      onChange={setDraft} label={t('settings.browser.advancedScripts', 'Allow advanced scripts')} />
    <p className="max-w-prose text-xs text-[var(--text-secondary)]">
      {t('settings.browser.scriptsExplanation', 'Allow the agent to run arbitrary JavaScript on this site, including signed-in page content.')}
    </p>
    <Button variant="secondary" size="sm" leading={<Save className="h-3.5 w-3.5" />}
      disabled={checking || pending || scripts === allowed} loading={pending} onClick={() => void save()}>
      {t('settings.browser.saveScripts', 'Save script permission')}
    </Button>
    {error && <p role="alert" className="break-words text-xs text-[var(--color-error)]">{error}</p>}
  </div>;
}
