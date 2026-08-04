/**
 * ModelDefaultsSettings — the GLOBAL DEFAULT model config (Settings › Model
 * Defaults).
 *
 * The provider + model + coding-agent framework every agent INHERITS by
 * default. Per-agent overrides live in the chat page (the model chip + the
 * header ⚙ → AgentLlmConfigPanel). This panel writes the user-level slots via
 * the unchanged endpoints:
 *   - PUT  /api/providers/slots/{agent|helper_llm}   (setProviderSlot)
 *   - POST /api/providers/agent-framework            (setAgentFramework)
 *
 * Extracted out of ProviderSettings' old "Section ③" so LLM Providers is purely
 * the credential wallet. Option-building is shared via lib/agentFramework so the
 * choices match the per-agent panel + the provider dropdowns.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import { useConfirm } from '@/components/ui';
import { useConfigStore } from '@/stores/configStore';
import {
  AGENT_FRAMEWORKS,
  availableFrameworks,
  providerBacksFramework,
  isCodexFramework,
  getModelsForSlot,
  prettifyModel,
  RECOMMENDED_HELPER_MODEL_BY_PROTOCOL,
  defaultHelperModel,
  cloudNetmindOnly,
  frameworkAllowedInCloud,
  isSlotBindableSource,
  DESKTOP_RELEASES_URL,
  type ProviderSummary,
} from '@/lib/agentFramework';

type AgentDraft = {
  provider_id: string;
  model: string;
  thinking: string;
  reasoning_effort: string;
};
type HelperDraft = { provider_id: string; model: string };

const EMPTY_AGENT: AgentDraft = { provider_id: '', model: '', thinking: '', reasoning_effort: '' };
const EMPTY_HELPER: HelperDraft = { provider_id: '', model: '' };

interface SlotCfg {
  provider_id?: string;
  model?: string;
  thinking?: string;
  reasoning_effort?: string;
}

interface Props {
  /** Jump to the LLM Providers settings section (switch the nav tab). */
  onManageProviders?: () => void;
}

export function ModelDefaultsSettings({ onManageProviders }: Props = {}) {
  const { t } = useTranslation();
  const role = useConfigStore((s) => s.role);
  const netmindOnly = cloudNetmindOnly(role);
  // Styled alert (same Dialog shell as the add-provider modal) — Tauri's
  // wry webview doesn't render window.alert, so never use the native one.
  const { alert: showNotice, dialog: noticeDialog } = useConfirm();
  const [providers, setProviders] = useState<Record<string, ProviderSummary>>({});
  const [framework, setFramework] = useState('claude_code');
  const [probe, setProbe] = useState<{ ok: boolean; detail: string } | null>(null);
  const [install, setInstall] = useState<{ action: string; reason: string } | null>(null);
  const [frameworkSaving, setFrameworkSaving] = useState(false);
  const [agentDraft, setAgentDraft] = useState<AgentDraft>(EMPTY_AGENT);
  const [helperDraft, setHelperDraft] = useState<HelperDraft>(EMPTY_HELPER);
  const [agentInitial, setAgentInitial] = useState<AgentDraft>(EMPTY_AGENT);
  const [helperInitial, setHelperInitial] = useState<HelperDraft>(EMPTY_HELPER);
  const [loading, setLoading] = useState(true);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      // No free-tier banner any more: the free tier is an ordinary provider
      // card, so these defaults are never preempted — what is set here is what
      // runs, on the free wallet just as on a user's own key.
      const [provRes, fwRes] = await Promise.all([
        api.getProviders(),
        api.getAgentFramework(),
      ]);
      const provMap = (provRes?.data?.providers ?? {}) as Record<string, ProviderSummary>;
      setProviders(provMap);
      const slots = (provRes?.data?.slots ?? {}) as Record<string, { config?: SlotCfg | null }>;
      const a = slots.agent?.config ?? null;
      const h = slots.helper_llm?.config ?? null;
      const agent: AgentDraft = {
        provider_id: a?.provider_id || '',
        model: a?.model || '',
        thinking: a?.thinking || '',
        reasoning_effort: a?.reasoning_effort || '',
      };
      const helper: HelperDraft = { provider_id: h?.provider_id || '', model: h?.model || '' };
      setAgentDraft(agent);
      setHelperDraft(helper);
      setAgentInitial(agent);
      setHelperInitial(helper);
      if (fwRes?.success) {
        setFramework(fwRes.data.framework);
        setProbe(fwRes.data.probe);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : t('pages.settings.modelDefaults.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { void load(); }, [load]);

  const providerList = Object.values(providers).filter((p) => p.is_active);
  const hasProviders = providerList.length > 0;

  // Agent slot: protocol + subscription-credential gate, both inside
  // providerBacksFramework (mirrors backend validate_slot_binding). No source
  // filter beyond that on local — any openai-protocol provider (codex_oauth /
  // user / netmind / yunwu / openrouter) can back codex; Responses-API
  // compatibility is the provider's concern, not policed here (binding rule
  // #15). Cloud non-staff additionally sees NetMind-source providers only
  // (cloudNetmindOnly — the route gates would 403 anything else).
  const bindableProviders = providerList.filter(
    (p) => !netmindOnly || isSlotBindableSource(p.source),
  );
  const agentProviders = bindableProviders.filter((p) =>
    providerBacksFramework(p, framework),
  );
  // Frameworks nothing bindable can drive are hidden rather than offered as a
  // dead end (a Claude Code Login alone can only ever run Claude Code).
  const frameworkOptions = availableFrameworks(bindableProviders, framework);
  const frameworksHidden = frameworkOptions.length < AGENT_FRAMEWORKS.length;
  // Helper accepts OAuth (claude_oauth / codex_oauth) too: the backend routes an
  // OAuth helper to a CliHelperConfig and runs its structured calls one-shot
  // through the same CLI as the agent, so one subscription covers both slots.
  const helperProviders = providerList.filter(
    (p) =>
      (!netmindOnly || isSlotBindableSource(p.source)) &&
      ['openai', 'anthropic'].includes(p.protocol),
  );

  const sameAgent = (a: AgentDraft, b: AgentDraft) =>
    a.provider_id === b.provider_id && a.model === b.model &&
    a.thinking === b.thinking && a.reasoning_effort === b.reasoning_effort;
  const sameHelper = (a: HelperDraft, b: HelperDraft) =>
    a.provider_id === b.provider_id && a.model === b.model;

  const agentChanged = !sameAgent(agentDraft, agentInitial);
  const helperChanged = !sameHelper(helperDraft, helperInitial);
  const isDirty = agentChanged || helperChanged;

  // Framework switch persists immediately (it may auto-install codex + re-probe
  // auth). The BACKEND decides whether the bound provider survives — a card the
  // new framework can't drive (CLI subscription, or wrong protocol) is unbound
  // server-side. Mirror that answer instead of clearing optimistically: a
  // binding both frameworks can drive must keep the user's model pick.
  const onFrameworkChange = async (next: string) => {
    setFramework(next);
    setFrameworkSaving(true);
    setError('');
    setInstall(null);
    try {
      const resp = await api.setAgentFramework(next);
      if (resp.success) {
        setProbe(resp.data.probe);
        setInstall(resp.data.install);
        if (resp.data.slot_cleared) {
          const cleared = { provider_id: '', model: '' };
          setAgentDraft((d) => ({ ...d, ...cleared }));
          setAgentInitial((d) => ({ ...d, ...cleared }));
        }
      }
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : t('pages.settings.modelDefaults.frameworkSwitchFailed'),
      );
    } finally {
      setFrameworkSaving(false);
    }
  };

  const apply = async () => {
    if (!isDirty || applying) return;
    if (agentChanged && (!agentDraft.provider_id || !agentDraft.model)) {
      setError(t('pages.settings.modelDefaults.pickAgentModel'));
      return;
    }
    if (helperChanged && (!helperDraft.provider_id || !helperDraft.model)) {
      setError(t('pages.settings.modelDefaults.pickHelperModel'));
      return;
    }
    setApplying(true);
    setError('');
    try {
      if (agentChanged) {
        const r = await api.setProviderSlot('agent', {
          provider_id: agentDraft.provider_id,
          model: agentDraft.model,
          thinking: agentDraft.thinking,
          reasoning_effort: agentDraft.reasoning_effort,
        });
        if (!r.success) { setError(r.detail || t('pages.settings.modelDefaults.saveFailed')); return; }
      }
      if (helperChanged) {
        const r = await api.setProviderSlot('helper_llm', {
          provider_id: helperDraft.provider_id,
          model: helperDraft.model,
        });
        if (!r.success) { setError(r.detail || t('pages.settings.modelDefaults.saveFailed')); return; }
      }
      await load();
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (e) {
      setError(e instanceof Error ? e.message : t('pages.settings.modelDefaults.saveFailed'));
    } finally {
      setApplying(false);
    }
  };

  const selectCls =
    'w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)] disabled:opacity-50';
  const labelCls = 'block text-xs text-[var(--text-tertiary)] mb-1';
  const btnPrimary =
    'px-5 py-2.5 text-sm font-medium rounded-lg bg-[var(--text-primary)] text-[var(--text-inverse)] hover:opacity-90 disabled:opacity-40 transition-colors';

  const helperRecModel =
    RECOMMENDED_HELPER_MODEL_BY_PROTOCOL[providers[helperDraft.provider_id]?.protocol || 'openai'] || 'gpt-5.4-mini';

  if (loading) {
    return (
      <p className="text-sm text-[var(--text-tertiary)]">
        {t('pages.settings.modelDefaults.loading')}
      </p>
    );
  }

  if (!hasProviders) {
    return (
      <p className="text-sm text-[var(--text-tertiary)]">
        {t('pages.settings.modelDefaults.noProvidersBefore')}{' '}
        {onManageProviders ? (
          <button type="button" onClick={onManageProviders} className="font-medium text-[var(--accent-primary)] underline underline-offset-2 hover:opacity-80">
            {t('pages.settings.modelDefaults.providersSection')}
          </button>
        ) : (
          <span className="font-medium">
            {t('pages.settings.modelDefaults.providersSection')}
          </span>
        )}{' '}
        {t('pages.settings.modelDefaults.noProvidersAfter')}
      </p>
    );
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm text-[var(--text-tertiary)]">
          {t('pages.settings.modelDefaults.defaultsDescription')}
        </p>
        {onManageProviders && (
          <button
            type="button"
            onClick={onManageProviders}
            className="shrink-0 text-xs text-[var(--accent-primary)] hover:opacity-80 whitespace-nowrap"
          >
            {t('pages.settings.modelDefaults.manageProviders')}
          </button>
        )}
      </div>

      {/* ---- Agent slot ---- */}
      <div className="p-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-tertiary)]">
        <div className="text-sm font-medium text-[var(--text-primary)] mb-3">
          {t('pages.settings.modelDefaults.agentMain')}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2">
            <label className={labelCls}>
              {t('pages.settings.modelDefaults.framework')}
              {probe && (
                <span
                  className={cn('ml-2 text-xs', probe.ok ? 'text-[var(--color-success)]' : 'text-[var(--color-error)]')}
                  title={probe.detail}
                >
                  {probe.ok
                    ? `✓ ${t('pages.settings.modelDefaults.authReady')}`
                    : `✗ ${t('pages.settings.modelDefaults.authMissing')}`}
                </span>
              )}
            </label>
            {/* Cloud non-staff: the CLI-backed frameworks are staff-only
                (backend 403s them — they would run on the image's shared CLI
                login); claude_code and NexusPower are allowed. The select stays
                interactive — a blocked pick pops an explanation and snaps back,
                which reads friendlier than a greyed-out control. */}
            <select
              className={selectCls}
              value={framework}
              disabled={frameworkSaving}
              onChange={(e) => {
                // Ask the shared predicate rather than re-deriving the rule.
                if (!frameworkAllowedInCloud(e.target.value, role)) {
                  void showNotice({
                    title: t(
                      'pages.settings.modelDefaults.cloudFrameworkLockedTitle',
                      'Staff only in cloud',
                    ),
                    message: (
                      <>
                        {t(
                          'pages.settings.modelDefaults.cloudFrameworkLocked',
                          'This framework signs in through a shared CLI login, so it is staff-only in cloud. Claude Code and NexusPower-beta both work here.',
                        )}{' '}
                        <a
                          href={DESKTOP_RELEASES_URL}
                          target="_blank"
                          rel="noreferrer"
                          className="font-medium text-[var(--accent-primary)] underline underline-offset-2 hover:opacity-80"
                        >
                          {t(
                            'pages.settings.modelDefaults.cloudNetmindOnlyLink',
                            'Download the local desktop version to use your own keys →',
                          )}
                        </a>
                      </>
                    ),
                  });
                  // Controlled value didn't change, so React won't re-render —
                  // snap the DOM select back to the current framework itself.
                  e.target.value = framework;
                  return;
                }
                void onFrameworkChange(e.target.value);
              }}
            >
              {frameworkOptions.map((f) => (
                <option key={f.id} value={f.id}>{f.label} — {f.desc}</option>
              ))}
            </select>
            {frameworksHidden && (
              <div className="text-xs text-[var(--text-tertiary)] mt-1">
                {t(
                  'pages.settings.modelDefaults.frameworkFilteredNote',
                  'Only the frameworks your connected providers can actually run are listed.',
                )}
              </div>
            )}
            {frameworkSaving && isCodexFramework(framework) && (
              <div className="text-xs text-[var(--text-tertiary)] mt-1 italic">
                {t('pages.settings.modelDefaults.verifyingCodex')}
              </div>
            )}
            {install && install.action === 'install_failed' && (
              <div className="text-xs text-[var(--color-error)] mt-1">
                {t('pages.settings.modelDefaults.codexUnavailable', { reason: install.reason })}
              </div>
            )}
            {probe && !probe.ok && !(install && install.action === 'install_failed') && (
              <div className="text-xs text-[var(--text-tertiary)] mt-1">{probe.detail}</div>
            )}
          </div>

          <div>
            <label className={labelCls}>{t('pages.settings.modelDefaults.provider')}</label>
            <select
              className={selectCls}
              value={agentDraft.provider_id}
              onChange={(e) => {
                const pid = e.target.value;
                const prov = providers[pid];
                const models = prov ? getModelsForSlot(prov, 'agent', framework, {}) : [];
                setAgentDraft((d) => ({ ...d, provider_id: pid, model: models[0]?.model_id || '' }));
              }}
            >
              <option value="">{t('pages.settings.modelDefaults.selectProvider')}</option>
              {agentProviders.map((p) => (<option key={p.provider_id} value={p.provider_id}>{p.name}</option>))}
            </select>
          </div>

          <div>
            <label className={labelCls}>{t('pages.settings.modelDefaults.model')}</label>
            <select
              className={selectCls}
              value={agentDraft.model}
              disabled={!agentDraft.provider_id}
              onChange={(e) => setAgentDraft((d) => ({ ...d, model: e.target.value }))}
            >
              <option value="">{t('pages.settings.modelDefaults.selectModel')}</option>
              {(providers[agentDraft.provider_id]
                ? getModelsForSlot(providers[agentDraft.provider_id], 'agent', framework, {})
                : []
              ).map((m) => (<option key={m.model_id} value={m.model_id}>{m.display_name}</option>))}
            </select>
          </div>

          <div>
            <label className={labelCls}>{t('pages.settings.modelDefaults.thinking')}</label>
            <select className={selectCls} value={agentDraft.thinking}
              onChange={(e) => setAgentDraft((d) => ({ ...d, thinking: e.target.value }))}>
              <option value="">{t('pages.settings.modelDefaults.autoDefault')}</option>
              <option value="on">{t('pages.settings.modelDefaults.on')}</option>
              <option value="off">{t('pages.settings.modelDefaults.off')}</option>
            </select>
          </div>

          <div>
            <label className={labelCls}>{t('pages.settings.modelDefaults.reasoningEffort')}</label>
            <select className={selectCls} value={agentDraft.reasoning_effort}
              onChange={(e) => setAgentDraft((d) => ({ ...d, reasoning_effort: e.target.value }))}>
              <option value="">{t('pages.settings.modelDefaults.autoDefault')}</option>
              <option value="low">{t('pages.settings.modelDefaults.low')}</option>
              <option value="medium">{t('pages.settings.modelDefaults.medium')}</option>
              <option value="high">{t('pages.settings.modelDefaults.high')}</option>
              <option value="max">{t('pages.settings.modelDefaults.max')}</option>
            </select>
          </div>
        </div>
      </div>

      {/* ---- Helper slot ---- */}
      <div className="p-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-tertiary)]">
        <div className="text-sm font-medium text-[var(--text-primary)] mb-3">
          {t('pages.settings.modelDefaults.helperTitle')}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={labelCls}>{t('pages.settings.modelDefaults.provider')}</label>
            <select
              className={selectCls}
              value={helperDraft.provider_id}
              onChange={(e) => {
                const pid = e.target.value;
                const prov = providers[pid];
                const models = prov ? getModelsForSlot(prov, 'helper_llm', null, {}) : [];
                const model = defaultHelperModel(prov?.source, prov?.protocol, models.map((m) => m.model_id));
                setHelperDraft({ provider_id: pid, model });
              }}
            >
              <option value="">{t('pages.settings.modelDefaults.selectProvider')}</option>
              {helperProviders.map((p) => (<option key={p.provider_id} value={p.provider_id}>{p.name}</option>))}
            </select>
          </div>
          <div>
            <label className={labelCls}>{t('pages.settings.modelDefaults.model')}</label>
            <select
              className={selectCls}
              value={helperDraft.model}
              disabled={!helperDraft.provider_id}
              onChange={(e) => setHelperDraft((d) => ({ ...d, model: e.target.value }))}
            >
              <option value="">{t('pages.settings.modelDefaults.selectModel')}</option>
              {(providers[helperDraft.provider_id]
                ? getModelsForSlot(providers[helperDraft.provider_id], 'helper_llm', null, {})
                : []
              ).map((m) => (<option key={m.model_id} value={m.model_id}>{m.display_name}</option>))}
            </select>
          </div>
        </div>
        <p className="text-xs text-[var(--text-tertiary)] mt-2">
          {t('pages.settings.modelDefaults.helperRecommendation', {
            model: prettifyModel(helperRecModel),
          })}
        </p>
      </div>

      {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}

      <div className="flex items-center gap-3">
        <button className={btnPrimary} disabled={!isDirty || applying} onClick={apply}>
          {applying
            ? t('pages.settings.modelDefaults.saving')
            : t('pages.settings.modelDefaults.saveDefaults')}
        </button>
        {saved && !isDirty && (
          <span className="text-sm text-[var(--color-success)]">
            ✓ {t('pages.settings.modelDefaults.saved')}
          </span>
        )}
      </div>

      {netmindOnly && (
        <p className="text-xs text-[var(--text-tertiary)]">
          {t(
            'pages.settings.modelDefaults.cloudNetmindOnlyNote',
            'The cloud version runs on your NetMind account — models from your own API keys are not available here.',
          )}{' '}
          <a
            href={DESKTOP_RELEASES_URL}
            target="_blank"
            rel="noreferrer"
            className="font-medium text-[var(--accent-primary)] underline underline-offset-2 hover:opacity-80"
          >
            {t(
              'pages.settings.modelDefaults.cloudNetmindOnlyLink',
              'Download the local desktop version to use your own keys →',
            )}
          </a>
        </p>
      )}

      {noticeDialog}
    </div>
  );
}
