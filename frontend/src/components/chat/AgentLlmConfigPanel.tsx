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
import { useCallback, useEffect, useId, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useFlashFlag } from '@/hooks/useFlashFlag';
import { Dialog, DialogContent, DialogFooter, useConfirm } from '@/components/ui';
import { api } from '@/lib/api';
import { refreshOnboardDefaults } from '@/lib/onboardDefaults';
import { useConfigStore } from '@/stores/configStore';
import {
  AGENT_FRAMEWORKS,
  availableFrameworks,
  providerBacksFramework,
  getModelsForSlot,
  prettifyModel,
  defaultHelperModel,
  defaultAgentModel,
  recommendedHelperModel,
  type OnboardDefaults,
  cloudNetmindOnly,
  frameworkAllowedInCloud,
  isSlotBindableSource,
  frameworkAvailabilityMap,
  withFrameworkAvailability,
  isFrameworkAvailable,
  DESKTOP_RELEASES_URL,
  type ProviderSummary,
  type LiveFrameworkEntry,
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
  // Filled by load() — no hardcoded framework id (see ownerFramework below).
  agent_framework: '',
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
  // Server-owned per-source default models (see lib/onboardDefaults); {} until loaded.
  const [onboardDefaults, setOnboardDefaults] = useState<OnboardDefaults>({});
  const [slots, setSlots] = useState<Record<string, AgentSlotView>>({});
  // Plugin-install gate (Claude Code / Codex CLI are user-installed local
  // plugins, not baked into the image) — name→available. This is an
  // account-wide fact, not per-agent, but the agent-framework endpoint is
  // the only place it's reported, so it's fetched alongside the per-agent
  // config on every open.
  const [frameworkAvailability, setFrameworkAvailability] = useState<Record<string, boolean>>({});
  // The live `frameworks[]` list itself (B6, 2026-09-07 final cut) —
  // `providerBacksFramework` reads `protocol`/`oauth_source` straight off it
  // and fails closed with no hardcoded fallback, so this stays `undefined`
  // (not `[]`) until a response actually lands.
  const [liveFrameworks, setLiveFrameworks] = useState<LiveFrameworkEntry[] | undefined>(undefined);
  const [agentDraft, setAgentDraft] = useState<Draft>(EMPTY_DRAFT);
  const knobIds = useId();
  const [helperDraft, setHelperDraft] = useState<Draft>(EMPTY_DRAFT);
  // Snapshot of what was loaded — so Save only writes slots the user changed.
  const [agentInitial, setAgentInitial] = useState<Draft>(EMPTY_DRAFT);
  const [helperInitial, setHelperInitial] = useState<Draft>(EMPTY_DRAFT);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  // Success feedback (GitHub #96): a Save that landed used to look
  // identical to a click that did nothing — the dialog stays open by
  // design (see file header), but nothing told the user it worked. Mirrors
  // ModelDefaultsSettings' "✓ Saved" indicator so the two editors read the
  // same way.
  const [saved, flashSaved] = useFlashFlag(2500);

  // `keepHelperDraft`: after a partial save, reload the stored state but keep
  // the user's still-unsaved helper edit in the draft.
  const load = useCallback(async (keepHelperDraft?: Draft) => {
    setLoading(true);
    setError('');
    try {
      const [cfgRes, provRes, fwRes, defaultsRes] = await Promise.all([
        api.getAgentLlmConfig(agentId),
        api.getProviders(),
        api.getAgentFramework(),
        // Per-source default models are server-owned; never rejects, reports ok.
        refreshOnboardDefaults(),
      ]);
      const provMap = (provRes?.data?.providers ?? {});
      setProviders(provMap);
      // Whole replace; a failed fetch keeps the previous table (and says so below).
      if (defaultsRes.ok) setOnboardDefaults(defaultsRes.defaults);
      const liveFwList = fwRes?.success ? fwRes.data.frameworks : undefined;
      setFrameworkAvailability(frameworkAvailabilityMap(liveFwList));
      setLiveFrameworks(liveFwList);
      const s = (cfgRes?.data?.slots ?? {}) as Record<string, AgentSlotView>;
      setSlots(s);
      // No owner slot bound → the framework the backend resolved for this
      // user (its default already skips uninstalled plugins). Never a
      // frontend literal: a hardcoded id can name a framework whose plugin
      // is not installed on this build.
      const ownerFramework =
        s.agent?.owner_default?.agent_framework || (fwRes?.success ? fwRes.data.framework : '');
      const a = draftFrom(s.agent?.effective ?? null, ownerFramework);
      // The helper slot carries no framework (its save never sends one).
      const h = draftFrom(s.helper_llm?.effective ?? null, '');
      setAgentDraft(a);
      setHelperDraft(keepHelperDraft ?? h);
      setAgentInitial(a);
      setHelperInitial(h);
      // A soft failure of the framework endpoint leaves no framework list to
      // offer (and providerBacksFramework fails closed on it), so the form
      // would render an empty framework select with no explanation. Say the
      // load failed instead.
      // The same goes for the server's onboarding defaults: without them a
      // NetMind card would silently auto-select its omni list head.
      if (!fwRes?.success || !defaultsRes.ok) setError(t('pages.settings.modelDefaults.loadFailed'));
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
    providerBacksFramework(p, agentDraft.agent_framework, liveFrameworks),
  );
  // Hide frameworks no bindable card can drive — picking one would leave the
  // provider dropdown empty and the save rejected. Plugin availability is
  // layered on top and never hides further (see ModelDefaultsSettings for
  // the shared rationale).
  const frameworkOptions = withFrameworkAvailability(
    availableFrameworks(bindableProviders, agentDraft.agent_framework, liveFrameworks),
    frameworkAvailability,
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
        if (!r.success) {
          const failure = r.detail || t('pages.settings.modelDefaults.saveFailed');
          if (!agentChanged) { setError(failure); return; }
          // The agent half landed: reload it as saved, keep the helper edit.
          await load(helperDraft);
          onSaved?.();
          setError(t('pages.settings.modelDefaults.agentSavedHelperFailed', { detail: failure }));
          return;
        }
      }
      await load();
      onSaved?.();
      flashSaved();
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
      flashSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : t('pages.settings.modelDefaults.resetFailed'));
    } finally {
      setSaving(false);
    }
  };

  const selectCls =
    'w-full px-3 py-2 text-sm rounded-[var(--radius-lg)] border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)] disabled:opacity-50';
  const labelCls = 'block text-xs text-[var(--text-tertiary)] mb-1';
  const btnPrimary =
    'px-4 py-2 text-sm font-medium rounded-[var(--radius-lg)] bg-[var(--text-primary)] text-[var(--text-inverse)] hover:opacity-90 disabled:opacity-40 transition-colors';
  const btnGhost =
    'px-3 py-2 text-sm rounded-[var(--radius-lg)] border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--nm-paper-warm)] disabled:opacity-40 transition-colors';
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

  const helperProvider = providers[helperDraft.provider_id];
  const helperRecModel = recommendedHelperModel(
    helperProvider?.source,
    helperProvider?.protocol,
    helperProvider ? getModelsForSlot(helperProvider, 'helper_llm', null, {}).map((m) => m.model_id) : [],
    onboardDefaults,
  );

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
            <p className="text-sm text-[var(--text-tertiary)]">
              {t('pages.settings.modelDefaults.agentOnlyDescription', { agentId })}
            </p>

            {/* ---- Agent slot ---- */}
            <div className="p-4 rounded-[var(--radius-xl)] border border-[var(--border-subtle)] bg-[var(--bg-tertiary)]">
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
                      const picked = frameworkOptions.find((f) => f.id === e.target.value);
                      // Plugin-install gate first — a framework backed by a
                      // local plugin (Claude Code / Codex CLI) the user
                      // hasn't installed can't be saved even though the
                      // native <option disabled> already blocks most UIs
                      // from reaching this branch.
                      if (picked && !isFrameworkAvailable(picked)) {
                        void showNotice({
                          title: t(
                            'pages.settings.modelDefaults.pluginRequiredTitle',
                            'Plugin required',
                          ),
                          message: (
                            <>
                              {t(
                                'pages.settings.modelDefaults.pluginRequired',
                                'This framework needs its plugin installed first.',
                              )}{' '}
                              <button
                                type="button"
                                onClick={() => { onClose(); navigate('/app/settings?tab=plugins'); }}
                                className="font-medium text-[var(--accent-primary)] underline underline-offset-2 hover:opacity-80"
                              >
                                {t('pages.settings.modelDefaults.pluginRequiredLink', 'Go to Settings › Plugins →')}
                              </button>
                            </>
                          ),
                        });
                        e.target.value = agentDraft.agent_framework;
                        return;
                      }
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
                      <option key={f.id} value={f.id} disabled={!isFrameworkAvailable(f)}>
                        {f.label} — {f.desc}
                        {!isFrameworkAvailable(f)
                          ? ` (${t('pages.settings.modelDefaults.pluginNotInstalledSuffix', 'plugin not installed')})`
                          : ''}
                      </option>
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
                        ...d, provider_id: pid, model: defaultAgentModel(prov?.source, models.map((m) => m.model_id), onboardDefaults),
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
                  {/* Thinking / reasoning effort belong to the slot binding:
                      editable only once a provider is chosen, like the model. */}
                  <label htmlFor={`${knobIds}-thinking`} className={labelCls}>{t('pages.settings.modelDefaults.thinking')}</label>
                  <select
                    id={`${knobIds}-thinking`}
                    className={selectCls}
                    value={agentDraft.thinking}
                    disabled={!agentDraft.provider_id}
                    onChange={(e) => setAgentDraft((d) => ({ ...d, thinking: e.target.value }))}
                  >
                    <option value="">{t('pages.settings.modelDefaults.autoDefault')}</option>
                    <option value="on">{t('pages.settings.modelDefaults.on')}</option>
                    <option value="off">{t('pages.settings.modelDefaults.off')}</option>
                  </select>
                </div>

                <div>
                  <label htmlFor={`${knobIds}-effort`} className={labelCls}>{t('pages.settings.modelDefaults.reasoningEffort')}</label>
                  <select
                    id={`${knobIds}-effort`}
                    className={selectCls}
                    value={agentDraft.reasoning_effort}
                    disabled={!agentDraft.provider_id}
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
            <div className="p-4 rounded-[var(--radius-xl)] border border-[var(--border-subtle)] bg-[var(--bg-tertiary)]">
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
                      const model = defaultHelperModel(prov?.source, prov?.protocol, models.map((m) => m.model_id), onboardDefaults);
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
        {saved && !isDirty && (
          <span className="text-sm text-[var(--color-success)]">
            ✓ {t('pages.settings.modelDefaults.saved')}
          </span>
        )}
      </DialogFooter>
    </Dialog>
    {noticeDialog}
    </>
  );
}
