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
 * The framework is a DRAFT like every other field: picking one writes nothing,
 * it makes the form dirty, and Save commits it (framework first, then the
 * slots — set_slot validates the provider against the stored framework).
 * Framework + agent slot are one unit from the user's view: if the slot write
 * fails after the framework landed, the framework (and a binding the switch
 * cleared) is rolled back; if even that fails, the page reloads the stored
 * state and says which half was saved.
 *
 * Extracted out of ProviderSettings' old "Section ③" so LLM Providers is purely
 * the credential wallet. Option-building is shared via lib/agentFramework so the
 * choices match the per-agent panel + the provider dropdowns.
 */
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useFlashFlag } from '@/hooks/useFlashFlag';
import { api } from '@/lib/api';
import { refreshOnboardDefaults } from '@/lib/onboardDefaults';
import { cn } from '@/lib/utils';
import { useConfirm } from '@/components/ui';
import { ApplyDefaultsToAgentsDialog } from './ApplyDefaultsToAgentsDialog';
import type { SlotOverrideStats } from '@/types/api';
import { useConfigStore } from '@/stores/configStore';
import {
  AGENT_FRAMEWORKS,
  availableFrameworks,
  providerBacksFramework,
  isCodexFramework,
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

/** Drafts a post-failure reload keeps instead of the stored values. */
interface KeepDrafts {
  framework?: string;
  agent?: AgentDraft;
  helper?: HelperDraft;
}

interface Props {
  /** Jump to the LLM Providers settings section (switch the nav tab). */
  onManageProviders?: () => void;
  /** Jump to the Plugins settings section (switch the nav tab). */
  onManagePlugins?: () => void;
}

export function ModelDefaultsSettings({ onManageProviders, onManagePlugins }: Props = {}) {
  const { t } = useTranslation();
  const role = useConfigStore((s) => s.role);
  const netmindOnly = cloudNetmindOnly(role);
  // Styled alert (same Dialog shell as the add-provider modal) — Tauri's
  // wry webview doesn't render window.alert, so never use the native one.
  const { alert: showNotice, dialog: noticeDialog } = useConfirm();
  const [providers, setProviders] = useState<Record<string, ProviderSummary>>({});
  // Server-owned per-source default models (see lib/onboardDefaults); {} until loaded.
  const [onboardDefaults, setOnboardDefaults] = useState<OnboardDefaults>({});
  // Framework draft + the stored value it is compared against. No hardcoded
  // initial id: the form renders only after load() filled both from the
  // backend, which resolves the user's framework (default included).
  const [framework, setFramework] = useState('');
  const [frameworkInitial, setFrameworkInitial] = useState('');
  // Plugin-install gate (Claude Code / Codex CLI are user-installed local
  // plugins) — name→available, defaulted to available for any framework the
  // backend didn't mention (see lib/agentFramework frameworkAvailabilityMap).
  const [frameworkAvailability, setFrameworkAvailability] = useState<Record<string, boolean>>({});
  // The live `frameworks[]` list itself (B6, 2026-09-07 final cut) — no longer
  // just consulted for plugin-install availability: `providerBacksFramework`
  // reads `protocol`/`oauth_source` straight off it and fails closed with no
  // hardcoded fallback, so this must stay `undefined` (not `[]`) until a
  // response actually lands.
  const [liveFrameworks, setLiveFrameworks] = useState<LiveFrameworkEntry[] | undefined>(undefined);
  const [probe, setProbe] = useState<{ ok: boolean; detail: string } | null>(null);
  const [install, setInstall] = useState<{ action: string; reason: string } | null>(null);
  const [frameworkSaving, setFrameworkSaving] = useState(false);
  const [agentDraft, setAgentDraft] = useState<AgentDraft>(EMPTY_AGENT);
  const [helperDraft, setHelperDraft] = useState<HelperDraft>(EMPTY_HELPER);
  const [agentInitial, setAgentInitial] = useState<AgentDraft>(EMPTY_AGENT);
  const [helperInitial, setHelperInitial] = useState<HelperDraft>(EMPTY_HELPER);
  const [applyStats, setApplyStats] = useState<SlotOverrideStats | null>(null);
  const [applyDirtySlots, setApplyDirtySlots] = useState<Array<'agent' | 'helper_llm'>>([]);
  const [loading, setLoading] = useState(true);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState('');
  const [saved, flashSaved] = useFlashFlag(2500);
  // Provider/model a framework switch dropped from the draft, kept so picking
  // a framework that can drive them again restores them.
  const droppedByFrameworkRef = useRef<{ provider_id: string; model: string } | null>(null);
  // The agent and helper blocks both carry "Provider" / "Model" labels; the
  // agent selects are named by the block title + their own label so each one
  // has a distinct accessible name.
  const agentIds = useId();
  const helperIds = useId();
  // `keep`: after a partial save, reload the stored state as the baseline but
  // keep the user's still-unsaved edits in the draft (so Save stays live and
  // nothing the user picked silently disappears).
  const load = useCallback(async (keep?: KeepDrafts) => {
    setLoading(true);
    setError('');
    try {
      // No free-tier banner any more: the free tier is an ordinary provider
      // card, so these defaults are never preempted — what is set here is what
      // runs, on the free wallet just as on a user's own key.
      const [provRes, fwRes, defaultsRes] = await Promise.all([
        api.getProviders(),
        api.getAgentFramework(),
        // Per-source default models are server-owned; never rejects, reports ok.
        refreshOnboardDefaults(),
      ]);
      const provMap = (provRes?.data?.providers ?? {});
      setProviders(provMap);
      // Whole replace; a failed fetch keeps the previous table (and says so below).
      if (defaultsRes.ok) setOnboardDefaults(defaultsRes.defaults);
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
      // A kept agent draft keeps the pair a framework switch dropped too, so
      // switching back still restores it after the reload.
      if (!keep?.agent) droppedByFrameworkRef.current = null;
      setAgentDraft(keep?.agent ?? agent);
      setHelperDraft(keep?.helper ?? helper);
      setAgentInitial(agent);
      setHelperInitial(helper);
      if (fwRes?.success) {
        setFramework(keep?.framework ?? fwRes.data.framework);
        setFrameworkInitial(fwRes.data.framework);
        setProbe(fwRes.data.probe);
        setFrameworkAvailability(frameworkAvailabilityMap(fwRes.data.frameworks));
        setLiveFrameworks(fwRes.data.frameworks);
      } else {
        // A soft failure of the framework endpoint leaves no framework list
        // (and providerBacksFramework fails closed on it), so both the
        // framework and the agent-provider selects would render empty with
        // no explanation. Say the load failed instead.
        setError(t('pages.settings.modelDefaults.loadFailed'));
      }
      // Without the server's onboarding defaults a NetMind card would silently
      // auto-select its omni list head: report it like the framework failure.
      if (!defaultsRes.ok) setError(t('pages.settings.modelDefaults.loadFailed'));
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
    providerBacksFramework(p, framework, liveFrameworks),
  );
  // Frameworks nothing bindable can drive are hidden rather than offered as a
  // dead end (a Claude Code Login alone can only ever run Claude Code).
  // Plugin availability is layered on TOP of that hiding gate and never
  // hides further — an uninstalled framework still needs to render so the
  // user can see it and go install it (disabled, not absent).
  const frameworkOptions = withFrameworkAvailability(
    availableFrameworks(bindableProviders, framework, liveFrameworks),
    frameworkAvailability,
  );
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

  const frameworkChanged = framework !== frameworkInitial;
  const agentChanged = !sameAgent(agentDraft, agentInitial);
  const helperChanged = !sameHelper(helperDraft, helperInitial);
  // The framework is part of the dirty state (Owner bug 2026-09-11): it used
  // to be written on change and left out of this check, so after picking a
  // new default framework Save stayed disabled and the page never confirmed
  // the choice — "changing the framework cannot be saved".
  const isDirty = frameworkChanged || agentChanged || helperChanged;

  // Picking a framework only edits the draft. A bound provider the new
  // framework cannot drive (wrong protocol, or a CLI subscription it cannot
  // redeem) is dropped from the draft — same predicate the provider dropdown
  // filters with — so the user picks one that can; a provider both frameworks
  // can drive keeps the user's model pick. The dropped pair is remembered and
  // put back when the user picks a framework that can drive it again (only
  // while no other provider was picked in between).
  const onFrameworkChange = (next: string) => {
    setFramework(next);
    setError('');
    setInstall(null);
    const bound = providers[agentDraft.provider_id];
    if (bound && !providerBacksFramework(bound, next, liveFrameworks)) {
      droppedByFrameworkRef.current = { provider_id: agentDraft.provider_id, model: agentDraft.model };
      setAgentDraft((d) => ({ ...d, provider_id: '', model: '' }));
      return;
    }
    const dropped = droppedByFrameworkRef.current;
    if (!agentDraft.provider_id && dropped) {
      const prov = providers[dropped.provider_id];
      if (prov && providerBacksFramework(prov, next, liveFrameworks)) {
        droppedByFrameworkRef.current = null;
        setAgentDraft((d) => ({ ...d, ...dropped }));
      }
    }
  };

  const errorText = (e: unknown) =>
    e instanceof Error ? e.message : t('pages.settings.modelDefaults.saveFailed');

  // Undo a framework write whose agent-slot write then failed: the stored
  // framework goes back, and so does the binding the switch cleared.
  //  - 'rolled-back': nothing is stored any more;
  //  - 'binding-lost': the framework is back, but the cleared binding could
  //    not be written back (the slot is now unbound);
  //  - 'failed': the framework could not be put back (it stays switched).
  const rollbackFramework = async (
    bindingCleared: boolean,
  ): Promise<'rolled-back' | 'binding-lost' | 'failed'> => {
    try {
      const back = await api.setAgentFramework(frameworkInitial);
      if (!back.success) return 'failed';
      // The probe follows the framework the moment it is back.
      setProbe(back.data.probe);
      setInstall(null);
    } catch {
      return 'failed';
    }
    if (!bindingCleared || !agentInitial.provider_id) return 'rolled-back';
    try {
      const r = await api.setProviderSlot('agent', {
        provider_id: agentInitial.provider_id,
        model: agentInitial.model,
        thinking: agentInitial.thinking,
        reasoning_effort: agentInitial.reasoning_effort,
      });
      return r.success ? 'rolled-back' : 'binding-lost';
    } catch {
      return 'binding-lost';
    }
  };

  const apply = async () => {
    if (!isDirty || applying) return;
    // Only an agent-slot edit needs a provider + model. A framework change on
    // its own is saved even while the agent slot is still unbound — the
    // backend keeps the framework on a stub slot row until one is wired. That
    // includes the case where the switch emptied the agent draft (the bound
    // card cannot run the new framework): the framework is saved first and
    // the page then asks for a card (`slotClearedPickModel`). A half-filled
    // agent draft is still refused before anything is written, and so is a
    // draft the USER emptied (picking the blank provider option, or editing
    // thinking/effort on an unbound slot): only an untouched agent draft, or
    // one the framework switch itself emptied (`droppedByFrameworkRef` set),
    // counts as framework-only — otherwise the edit would be skipped while
    // the page still flashes "Saved".
    const agentDraftEmpty = !agentDraft.provider_id && !agentDraft.model;
    const frameworkOnlyAgent =
      frameworkChanged &&
      agentDraftEmpty &&
      (!agentChanged || droppedByFrameworkRef.current !== null);
    if (agentChanged && !frameworkOnlyAgent && (!agentDraft.provider_id || !agentDraft.model)) {
      setError(t('pages.settings.modelDefaults.pickAgentModel'));
      return;
    }
    if (helperChanged && (!helperDraft.provider_id || !helperDraft.model)) {
      setError(t('pages.settings.modelDefaults.pickHelperModel'));
      return;
    }
    setApplying(true);
    setError('');
    // Capture which slots changed BEFORE load() resets the initial snapshots —
    // only these are offered in the apply-to-agents dialog.
    // An agent slot emptied by the switch has nothing to push onto agents.
    const dirtySlots: Array<'agent' | 'helper_llm'> = [
      ...((agentChanged || frameworkChanged) && !agentDraftEmpty ? ['agent' as const] : []),
      ...(helperChanged ? ['helper_llm' as const] : []),
    ];
    try {
      // Framework FIRST: set_slot validates the agent provider against the
      // user's stored framework. The backend may still unbind the stored
      // provider (slot_cleared) — the draft's provider is then written below
      // even if the slot fields themselves were untouched.
      let slotCleared = false;
      if (frameworkChanged) {
        setFrameworkSaving(true);
        try {
          const resp = await api.setAgentFramework(framework);
          if (!resp.success) { setError(t('pages.settings.modelDefaults.frameworkSwitchFailed')); return; }
          setProbe(resp.data.probe);
          setInstall(resp.data.install);
          slotCleared = resp.data.slot_cleared;
        } finally {
          setFrameworkSaving(false);
        }
      }
      // The switch unbound a card the draft has no replacement for: the
      // framework is saved and the slot is now empty — no slot write, the
      // page ends by asking for a card.
      const needsCard = frameworkOnlyAgent && slotCleared;
      if ((agentChanged || slotCleared) && !agentDraftEmpty) {
        let failure = '';
        try {
          const r = await api.setProviderSlot('agent', {
            provider_id: agentDraft.provider_id,
            model: agentDraft.model,
            thinking: agentDraft.thinking,
            reasoning_effort: agentDraft.reasoning_effort,
          });
          if (!r.success) failure = r.detail || t('pages.settings.modelDefaults.saveFailed');
        } catch (e) {
          failure = errorText(e);
        }
        if (failure) {
          if (!frameworkChanged) { setError(failure); return; }
          const undo = await rollbackFramework(slotCleared);
          if (undo === 'rolled-back') {
            // Nothing is stored any more; the draft stays as the user left it.
            setError(t('pages.settings.modelDefaults.slotSaveRolledBack', { detail: failure }));
            return;
          }
          // Something half-landed: reload the stored state as the baseline,
          // keep every edit the user made so Save can retry it.
          await load({ framework, agent: agentDraft, helper: helperDraft });
          setError(t(
            undo === 'binding-lost'
              ? 'pages.settings.modelDefaults.frameworkRestoredBindingLost'
              : 'pages.settings.modelDefaults.frameworkSavedSlotFailed',
            { detail: failure },
          ));
          return;
        }
      }
      if (helperChanged) {
        let failure = '';
        try {
          const r = await api.setProviderSlot('helper_llm', {
            provider_id: helperDraft.provider_id,
            model: helperDraft.model,
          });
          if (!r.success) failure = r.detail || t('pages.settings.modelDefaults.saveFailed');
        } catch (e) {
          failure = errorText(e);
        }
        if (failure) {
          if (!frameworkChanged && !agentChanged) { setError(failure); return; }
          // The other half landed: show it as saved, keep the helper edit, and
          // name what actually landed (the agent slot, or only the framework).
          await load({ helper: helperDraft });
          setError(t(
            agentChanged && !agentDraftEmpty
              ? 'pages.settings.modelDefaults.agentSavedHelperFailed'
              : 'pages.settings.modelDefaults.frameworkSavedHelperFailed',
            { detail: failure },
          ));
          return;
        }
      }
      await load();
      if (needsCard) {
        setError(t('pages.settings.modelDefaults.slotClearedPickModel'));
        return;
      }
      flashSaved();
      // Offer to push the new default onto existing agents (clear-to-inherit).
      // Isolated try/catch: the save already succeeded, so a flaky stats GET
      // must NOT flip the UI to an error state — it just skips the dialog.
      try {
        const s = (await api.getSlotOverrideStats()).data;
        // Only offer the dialog if a slot the user CHANGED has overrides.
        const dirtyHasOverrides =
          (dirtySlots.includes('agent') && (s?.agent ?? 0) > 0) ||
          (dirtySlots.includes('helper_llm') && (s?.helper_llm ?? 0) > 0);
        if (s && dirtyHasOverrides) {
          setApplyDirtySlots(dirtySlots);
          setApplyStats(s); // opens the dialog
        }
      } catch {
        /* preview failed — the default is saved; just don't offer the dialog */
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setApplying(false);
    }
  };

  const selectCls =
    'w-full px-3 py-2 text-sm rounded-[var(--radius-lg)] border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)] disabled:opacity-50';
  const labelCls = 'block text-xs text-[var(--text-tertiary)] mb-1';
  const btnPrimary =
    'px-5 py-2.5 text-sm font-medium rounded-[var(--radius-lg)] bg-[var(--text-primary)] text-[var(--text-inverse)] hover:opacity-90 disabled:opacity-40 transition-colors';

  const helperProvider = providers[helperDraft.provider_id];
  const helperRecModel = recommendedHelperModel(
    helperProvider?.source,
    helperProvider?.protocol,
    helperProvider ? getModelsForSlot(helperProvider, 'helper_llm', null, {}).map((m) => m.model_id) : [],
    onboardDefaults,
  );

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
    <div className="space-y-6">
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
      <div className="p-4 rounded-[var(--radius-xl)] border border-[var(--border-subtle)] bg-[var(--bg-tertiary)]">
        <div id={`${agentIds}-title`} className="text-sm font-medium text-[var(--text-primary)] mb-3">
          {t('pages.settings.modelDefaults.agentMain')}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2">
            <label id={`${agentIds}-framework`} htmlFor={`${agentIds}-framework-select`} className={labelCls}>
              {t('pages.settings.modelDefaults.framework')}
              {probe && !frameworkChanged && (
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
              id={`${agentIds}-framework-select`}
              aria-labelledby={`${agentIds}-title ${agentIds}-framework`}
              className={selectCls}
              value={framework}
              disabled={frameworkSaving || applying}
              onChange={(e) => {
                const picked = frameworkOptions.find((f) => f.id === e.target.value);
                // Plugin-install gate first: a framework that needs a local
                // plugin (Claude Code / Codex CLI) the user hasn't installed
                // yet cannot be selected — even a native <option disabled>
                // doesn't stop a screen reader / programmatic change event,
                // so this re-checks server truth before saving.
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
                        {onManagePlugins ? (
                          <button
                            type="button"
                            onClick={onManagePlugins}
                            className="font-medium text-[var(--accent-primary)] underline underline-offset-2 hover:opacity-80"
                          >
                            {t('pages.settings.modelDefaults.pluginRequiredLink', 'Go to Settings › Plugins →')}
                          </button>
                        ) : (
                          t('pages.settings.modelDefaults.pluginRequiredLink', 'Go to Settings › Plugins →')
                        )}
                      </>
                    ),
                  });
                  e.target.value = framework;
                  return;
                }
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
                onFrameworkChange(e.target.value);
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
            {probe && !probe.ok && !frameworkChanged && !(install && install.action === 'install_failed') && (
              <div className="text-xs text-[var(--text-tertiary)] mt-1">{probe.detail}</div>
            )}
          </div>

          <div>
            <label id={`${agentIds}-provider`} htmlFor={`${agentIds}-provider-select`} className={labelCls}>{t('pages.settings.modelDefaults.provider')}</label>
            <select
              id={`${agentIds}-provider-select`}
              aria-labelledby={`${agentIds}-title ${agentIds}-provider`}
              className={selectCls}
              value={agentDraft.provider_id}
              onChange={(e) => {
                const pid = e.target.value;
                const prov = providers[pid];
                const models = prov ? getModelsForSlot(prov, 'agent', framework, {}) : [];
                setAgentDraft((d) => ({ ...d, provider_id: pid, model: defaultAgentModel(prov?.source, models.map((m) => m.model_id), onboardDefaults) }));
              }}
            >
              <option value="">{t('pages.settings.modelDefaults.selectProvider')}</option>
              {agentProviders.map((p) => (<option key={p.provider_id} value={p.provider_id}>{p.name}</option>))}
            </select>
          </div>

          <div>
            <label id={`${agentIds}-model`} htmlFor={`${agentIds}-model-select`} className={labelCls}>{t('pages.settings.modelDefaults.model')}</label>
            <select
              id={`${agentIds}-model-select`}
              aria-labelledby={`${agentIds}-title ${agentIds}-model`}
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
            {/* Thinking / reasoning effort live on the slot row, so they are
                only editable once a provider is chosen — like the model
                select. An edit on an unbound slot would have nowhere to land. */}
            <label id={`${agentIds}-thinking`} htmlFor={`${agentIds}-thinking-select`} className={labelCls}>{t('pages.settings.modelDefaults.thinking')}</label>
            <select
              id={`${agentIds}-thinking-select`}
              aria-labelledby={`${agentIds}-title ${agentIds}-thinking`}
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
            <label id={`${agentIds}-effort`} htmlFor={`${agentIds}-effort-select`} className={labelCls}>{t('pages.settings.modelDefaults.reasoningEffort')}</label>
            <select
              id={`${agentIds}-effort-select`}
              aria-labelledby={`${agentIds}-title ${agentIds}-effort`}
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
      </div>

      {/* ---- Helper slot ---- */}
      <div className="p-4 rounded-[var(--radius-xl)] border border-[var(--border-subtle)] bg-[var(--bg-tertiary)]">
        <div id={`${helperIds}-title`} className="text-sm font-medium text-[var(--text-primary)] mb-3">
          {t('pages.settings.modelDefaults.helperTitle')}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label id={`${helperIds}-provider`} htmlFor={`${helperIds}-provider-select`} className={labelCls}>{t('pages.settings.modelDefaults.provider')}</label>
            <select
              id={`${helperIds}-provider-select`}
              aria-labelledby={`${helperIds}-title ${helperIds}-provider`}
              className={selectCls}
              value={helperDraft.provider_id}
              onChange={(e) => {
                const pid = e.target.value;
                const prov = providers[pid];
                const models = prov ? getModelsForSlot(prov, 'helper_llm', null, {}) : [];
                const model = defaultHelperModel(prov?.source, prov?.protocol, models.map((m) => m.model_id), onboardDefaults);
                setHelperDraft({ provider_id: pid, model });
              }}
            >
              <option value="">{t('pages.settings.modelDefaults.selectProvider')}</option>
              {helperProviders.map((p) => (<option key={p.provider_id} value={p.provider_id}>{p.name}</option>))}
            </select>
          </div>
          <div>
            <label id={`${helperIds}-model`} htmlFor={`${helperIds}-model-select`} className={labelCls}>{t('pages.settings.modelDefaults.model')}</label>
            <select
              id={`${helperIds}-model-select`}
              aria-labelledby={`${helperIds}-title ${helperIds}-model`}
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

      {applyStats && (
        <ApplyDefaultsToAgentsDialog
          isOpen
          stats={applyStats}
          dirtySlots={applyDirtySlots}
          onClose={() => setApplyStats(null)}
          onApply={async (slots) => {
            // request() throws on non-2xx, so catch here — the dialog's apply()
            // has no catch of its own and would otherwise leave an unhandled
            // rejection with the dialog stuck open.
            try {
              const r = await api.applySlotsToAgents(slots);
              if (!r.success) {
                setError(r.detail || t('pages.settings.modelDefaults.saveFailed'));
                return;
              }
              // Echo the irreversible result so it isn't a silent close.
              const cleared = Object.values(r.data?.cleared ?? {}).reduce((a, b) => a + b, 0);
              void showNotice({
                title: t('pages.settings.modelDefaults.applyDoneTitle', 'Overrides cleared'),
                message: t('pages.settings.modelDefaults.applyDone', 'Cleared per-agent overrides — {{n}} agents affected.', { n: cleared }),
              });
            } catch (e) {
              setError(e instanceof Error ? e.message : t('pages.settings.modelDefaults.saveFailed'));
            }
          }}
        />
      )}
    </div>
  );
}
