/**
 * AgentLlmConfigPanel — per-agent LLM config editor (modal).
 *
 * Each agent independently picks its coding-agent framework + model (agent
 * slot), its reasoning knobs, and its helper model (helper_llm slot). Any slot
 * left as "inherit" falls back to the owner's global default (Settings ›
 * Providers). Writes to the per-agent override endpoints
 * (/api/agents/{id}/llm-config); changes apply on the agent's next run.
 *
 * ONE Save button applies the whole panel — it only writes the slots the user
 * actually changed, so touching the agent model doesn't turn an inheriting
 * helper into a custom one. Provider/model options are built with the shared
 * helpers in lib/agentFramework so this offers exactly the same choices the
 * Settings default editor does.
 */
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Dialog, DialogContent, DialogFooter, useConfirm } from '@/components/ui';
import { api } from '@/lib/api';
import { useConfigStore } from '@/stores/configStore';
import {
  AGENT_FRAMEWORKS,
  availableFrameworks,
  providerBacksFramework,
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
import type { AgentSlotView, AgentSlotEffective } from '@/types';

interface Props {
  agentId: string;
  isOpen: boolean;
  onClose: () => void;
  /** Fired after any successful save/reset so callers (badge) can refresh. */
  onSaved?: () => void;
}

type Draft = {
  provider_id: string;
  model: string;
  thinking: string;
  reasoning_effort: string;
  agent_framework: string; // agent slot only
};

const EMPTY_DRAFT: Draft = {
  provider_id: '',
  model: '',
  thinking: '',
  reasoning_effort: '',
  agent_framework: 'claude_code',
};

function draftFrom(eff: AgentSlotEffective | null, fallbackFramework: string): Draft {
  if (!eff) return { ...EMPTY_DRAFT, agent_framework: fallbackFramework };
  return {
    provider_id: eff.provider_id || '',
    model: eff.model || '',
    thinking: eff.thinking || '',
    reasoning_effort: eff.reasoning_effort || '',
    agent_framework: eff.agent_framework || fallbackFramework,
  };
}

const sameDraft = (a: Draft, b: Draft) =>
  a.provider_id === b.provider_id &&
  a.model === b.model &&
  a.thinking === b.thinking &&
  a.reasoning_effort === b.reasoning_effort &&
  a.agent_framework === b.agent_framework;

export function AgentLlmConfigPanel({ agentId, isOpen, onClose, onSaved }: Props) {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const role = useConfigStore((s) => s.role);
  const netmindOnly = cloudNetmindOnly(role);
  // Styled alert (same Dialog shell as the add-provider modal) — Tauri's
  // wry webview doesn't render window.alert, so never use the native one.
  const { alert: showNotice, dialog: noticeDialog } = useConfirm();
  const [providers, setProviders] = useState<Record<string, ProviderSummary>>({});
  const [slots, setSlots] = useState<Record<string, AgentSlotView>>({});
  const [agentDraft, setAgentDraft] = useState<Draft>(EMPTY_DRAFT);
  const [helperDraft, setHelperDraft] = useState<Draft>(EMPTY_DRAFT);
  // Snapshot of what was loaded — so Save only writes slots the user changed.
  const [agentInitial, setAgentInitial] = useState<Draft>(EMPTY_DRAFT);
  const [helperInitial, setHelperInitial] = useState<Draft>(EMPTY_DRAFT);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  // While the owner's cloud free tier has budget, the runtime pins every run to
  // the fixed system model and ignores what's edited here — surface that
  // honestly (the edits still persist and apply once the free tier is spent).
  const [freeTier, setFreeTier] = useState<{ active: boolean; model: string | null }>({
    active: false,
    model: null,
  });

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [cfgRes, provRes] = await Promise.all([
        api.getAgentLlmConfig(agentId),
        api.getProviders(),
      ]);
      const provMap = (provRes?.data?.providers ?? {}) as Record<string, ProviderSummary>;
      setProviders(provMap);
      const s = (cfgRes?.data?.slots ?? {}) as Record<string, AgentSlotView>;
      setSlots(s);
      setFreeTier(cfgRes?.data?.free_tier ?? { active: false, model: null });
      const ownerFramework =
        s.agent?.owner_default?.agent_framework || 'claude_code';
      const a = draftFrom(s.agent?.effective ?? null, ownerFramework);
      const h = draftFrom(s.helper_llm?.effective ?? null, 'claude_code');
      setAgentDraft(a);
      setHelperDraft(h);
      setAgentInitial(a);
      setHelperInitial(h);
    } catch (e) {
      setError(e instanceof Error ? e.message : t('pages.settings.modelDefaults.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [agentId, t]);

  useEffect(() => {
    if (isOpen) void load();
  }, [isOpen, load]);

  const providerList = Object.values(providers).filter((p) => p.is_active);

  // Agent slot: protocol + subscription-credential gate, both inside
  // providerBacksFramework (mirrors backend validate_slot_binding). No further
  // source filter on local — any openai-protocol provider (codex_oauth / user /
  // netmind / yunwu / openrouter) can back codex; runtime Responses-API
  // compatibility is the provider's characteristic, not gated here (binding
  // rule #15). Cloud non-staff additionally sees NetMind-source providers only
  // (cloudNetmindOnly — the route gate would 403 anything else).
  const bindableProviders = providerList.filter(
    (p) => !netmindOnly || isSlotBindableSource(p.source),
  );
  const agentProviders = bindableProviders.filter((p) =>
    providerBacksFramework(p, agentDraft.agent_framework),
  );
  // Hide frameworks no bindable card can drive — picking one would leave the
  // provider dropdown empty and the save rejected.
  const frameworkOptions = availableFrameworks(
    bindableProviders, agentDraft.agent_framework,
  );
  const frameworksHidden = frameworkOptions.length < AGENT_FRAMEWORKS.length;

  // Helper slot: openai/anthropic protocols. OAuth providers (claude_oauth /
  // codex_oauth) are allowed too — the backend routes an OAuth helper to a
  // CliHelperConfig and runs its structured calls one-shot through the same CLI
  // as the agent, so one subscription covers both slots.
  const helperProviders = providerList.filter(
    (p) =>
      (!netmindOnly || isSlotBindableSource(p.source)) &&
      ['openai', 'anthropic'].includes(p.protocol),
  );

  const agentChanged = !sameDraft(agentDraft, agentInitial);
  const helperChanged = !sameDraft(helperDraft, helperInitial);
  const isDirty = agentChanged || helperChanged;

  // Save writes ONLY the slots the user actually changed — so editing the
  // agent model doesn't silently create a helper override.
  const saveAll = async () => {
    if (!isDirty || saving) return;
    // Validate changed slots before writing any.
    if (agentChanged && (!agentDraft.provider_id || !agentDraft.model)) {
      setError(t('pages.settings.modelDefaults.pickAgentModel'));
      return;
    }
    if (helperChanged && (!helperDraft.provider_id || !helperDraft.model)) {
      setError(t('pages.settings.modelDefaults.pickHelperModel'));
      return;
    }
    setSaving(true);
    setError('');
    try {
      if (agentChanged) {
        const r = await api.setAgentLlmConfig(agentId, 'agent', {
          provider_id: agentDraft.provider_id,
          model: agentDraft.model,
          thinking: agentDraft.thinking,
          reasoning_effort: agentDraft.reasoning_effort,
          agent_framework: agentDraft.agent_framework,
        });
        if (!r.success) { setError(r.detail || t('pages.settings.modelDefaults.saveFailed')); return; }
      }
      if (helperChanged) {
        const r = await api.setAgentLlmConfig(agentId, 'helper_llm', {
          provider_id: helperDraft.provider_id,
          model: helperDraft.model,
        });
        if (!r.success) { setError(r.detail || t('pages.settings.modelDefaults.saveFailed')); return; }
      }
      await load();
      onSaved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : t('pages.settings.modelDefaults.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const resetSlot = async (slot: 'agent' | 'helper_llm') => {
    setSaving(true);
    setError('');
    try {
      await api.resetAgentLlmConfig(agentId, slot);
      await load();
      onSaved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : t('pages.settings.modelDefaults.resetFailed'));
    } finally {
      setSaving(false);
    }
  };

  const selectCls =
    'w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)] disabled:opacity-50';
  const labelCls = 'block text-xs text-[var(--text-tertiary)] mb-1';
  const btnPrimary =
    'px-4 py-2 text-sm font-medium rounded-lg bg-[var(--text-primary)] text-[var(--text-inverse)] hover:opacity-90 disabled:opacity-40 transition-colors';
  const btnGhost =
    'px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] disabled:opacity-40 transition-colors';
  const resetLink =
    'text-xs text-[var(--text-tertiary)] underline decoration-dotted hover:text-[var(--color-error)] transition-colors disabled:opacity-40';

  const StatusChip = ({ view }: { view?: AgentSlotView }) =>
    view?.inheriting !== false ? (
      <span className="text-xs text-[var(--text-tertiary)]">
        {t('pages.settings.modelDefaults.inheritingDefault')}
      </span>
    ) : (
      <span className="text-xs text-[var(--accent-primary)]">
        {t('pages.settings.modelDefaults.customForAgent')}
      </span>
    );

  const helperRecModel =
    RECOMMENDED_HELPER_MODEL_BY_PROTOCOL[
      providers[helperDraft.provider_id]?.protocol || 'openai'
    ] || 'gpt-5.4-mini';

  return (
    <>
    <Dialog
      isOpen={isOpen}
      onClose={onClose}
      title={t('pages.settings.modelDefaults.panelTitle')}
      size="2xl"
    >
      <DialogContent>
        {loading ? (
          <p className="text-sm text-[var(--text-tertiary)]">
            {t('pages.settings.modelDefaults.loading')}
          </p>
        ) : (
          <div className="space-y-6">
            {freeTier.active && (
              <div className="rounded-xl border border-[var(--accent-primary)]/40 bg-[var(--accent-primary)]/10 px-4 py-3 text-sm text-[var(--text-secondary)]">
                {t('chat.model.freeTierBanner', {
                  model: freeTier.model ? prettifyModel(freeTier.model) : '',
                })}
              </div>
            )}
            <p className="text-sm text-[var(--text-tertiary)]">
              {t('pages.settings.modelDefaults.agentOnlyDescription', { agentId })}
            </p>

            {/* ---- Agent slot ---- */}
            <div className="p-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-tertiary)]">
              <div className="flex items-center justify-between mb-3">
                <span className="text-sm font-medium text-[var(--text-primary)]">
                  {t('pages.settings.modelDefaults.agentMain')}
                </span>
                <StatusChip view={slots.agent} />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="col-span-2">
                  <label className={labelCls}>
                    {t('pages.settings.modelDefaults.framework')}
                  </label>
                  {/* Cloud non-staff: the CLI-backed frameworks are blocked
                      (they would run on the image's shared CLI login);
                      claude_code and NexusPower are allowed. Blocked picks pop
                      an explanation and snap back, friendlier than a greyed-out
                      control. Mirrors the backend 403 via cloud_policy's twin. */}
                  <select
                    className={selectCls}
                    value={agentDraft.agent_framework}
                    onChange={(e) => {
                      // Ask the shared predicate, never re-derive the rule:
                      // the inlined `!== 'claude_code'` here and in
                      // ModelDefaultsSettings both kept rejecting NexusPower
                      // after it became cloud-legal.
                      if (!frameworkAllowedInCloud(e.target.value, role)) {
                        void showNotice({
                          title: t(
                            'pages.settings.modelDefaults.cloudFrameworkLockedTitle',
                            'Staff only in cloud',
                          ),
                          message: t(
                            'pages.settings.modelDefaults.cloudFrameworkLocked',
                            'This framework signs in through a shared CLI login, so it is staff-only in cloud. Claude Code and NexusPower-beta both work here.',
                          ),
                        });
                        // Controlled value didn't change → no re-render;
                        // snap the DOM select back by hand.
                        e.target.value = agentDraft.agent_framework;
                        return;
                      }
                      // Switching framework can invalidate the provider (protocol
                      // change) — clear it so the user re-picks a compatible one.
                      setAgentDraft((d) => ({
                        ...d, agent_framework: e.target.value, provider_id: '', model: '',
                      }));
                    }}
                  >
                    {frameworkOptions.map((f) => (
                      <option key={f.id} value={f.id}>{f.label} — {f.desc}</option>
                    ))}
                  </select>
                  {frameworksHidden && (
                    <p className="text-xs text-[var(--text-tertiary)] mt-1">
                      {t(
                        'pages.settings.modelDefaults.frameworkFilteredNote',
                        'Only the frameworks your connected providers can actually run are listed.',
                      )}
                    </p>
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
                      const models = prov
                        ? getModelsForSlot(prov, 'agent', agentDraft.agent_framework, {})
                        : [];
                      setAgentDraft((d) => ({
                        ...d, provider_id: pid, model: models[0]?.model_id || '',
                      }));
                    }}
                  >
                    <option value="">{t('pages.settings.modelDefaults.selectProvider')}</option>
                    {agentProviders.map((p) => (
                      <option key={p.provider_id} value={p.provider_id}>{p.name}</option>
                    ))}
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
                      ? getModelsForSlot(providers[agentDraft.provider_id], 'agent', agentDraft.agent_framework, {})
                      : []
                    ).map((m) => (
                      <option key={m.model_id} value={m.model_id}>{m.display_name}</option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className={labelCls}>{t('pages.settings.modelDefaults.thinking')}</label>
                  <select
                    className={selectCls}
                    value={agentDraft.thinking}
                    onChange={(e) => setAgentDraft((d) => ({ ...d, thinking: e.target.value }))}
                  >
                    <option value="">{t('pages.settings.modelDefaults.autoDefault')}</option>
                    <option value="on">{t('pages.settings.modelDefaults.on')}</option>
                    <option value="off">{t('pages.settings.modelDefaults.off')}</option>
                  </select>
                </div>

                <div>
                  <label className={labelCls}>{t('pages.settings.modelDefaults.reasoningEffort')}</label>
                  <select
                    className={selectCls}
                    value={agentDraft.reasoning_effort}
                    onChange={(e) => setAgentDraft((d) => ({ ...d, reasoning_effort: e.target.value }))}
                  >
                    <option value="">{t('pages.settings.modelDefaults.autoDefault')}</option>
                    <option value="low">{t('pages.settings.modelDefaults.low')}</option>
                    <option value="medium">{t('pages.settings.modelDefaults.medium')}</option>
                    <option value="high">{t('pages.settings.modelDefaults.high')}</option>
                    <option value="max">{t('pages.settings.modelDefaults.max')}</option>
                  </select>
                </div>
              </div>

              {slots.agent?.inheriting === false && (
                <div className="mt-3">
                  <button className={resetLink} disabled={saving} onClick={() => resetSlot('agent')}>
                    {t('pages.settings.modelDefaults.resetSlot')}
                  </button>
                </div>
              )}
            </div>

            {/* ---- Helper slot ---- */}
            <div className="p-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-tertiary)]">
              <div className="flex items-center justify-between mb-3">
                <span className="text-sm font-medium text-[var(--text-primary)]">
                  {t('pages.settings.modelDefaults.helperTitle')}
                </span>
                <StatusChip view={slots.helper_llm} />
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
                      setHelperDraft((d) => ({ ...d, provider_id: pid, model }));
                    }}
                  >
                    <option value="">{t('pages.settings.modelDefaults.selectProvider')}</option>
                    {helperProviders.map((p) => (
                      <option key={p.provider_id} value={p.provider_id}>{p.name}</option>
                    ))}
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
                    ).map((m) => (
                      <option key={m.model_id} value={m.model_id}>{m.display_name}</option>
                    ))}
                  </select>
                </div>
              </div>
              <p className="text-xs text-[var(--text-tertiary)] mt-2">
                {t('pages.settings.modelDefaults.helperRecommendation', {
                  model: prettifyModel(helperRecModel),
                })}
              </p>

              {slots.helper_llm?.inheriting === false && (
                <div className="mt-3">
                  <button className={resetLink} disabled={saving} onClick={() => resetSlot('helper_llm')}>
                    {t('pages.settings.modelDefaults.resetSlot')}
                  </button>
                </div>
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

            {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          </div>
        )}
      </DialogContent>
      <DialogFooter>
        <button
          type="button"
          className="mr-auto text-xs text-[var(--accent-primary)] hover:opacity-80"
          onClick={() => { onClose(); navigate('/app/settings'); }}
        >
          {t('pages.settings.modelDefaults.manageProviders')}
        </button>
        <button className={btnGhost} onClick={onClose}>
          {t('pages.settings.modelDefaults.close')}
        </button>
        <button className={btnPrimary} disabled={!isDirty || saving || loading} onClick={saveAll}>
          {saving
            ? t('pages.settings.modelDefaults.saving')
            : t('pages.settings.modelDefaults.save')}
        </button>
      </DialogFooter>
    </Dialog>
    {noticeDialog}
    </>
  );
}
