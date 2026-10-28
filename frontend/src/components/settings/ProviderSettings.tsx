/**
 * @file ProviderSettings.tsx
 * @description LLM Providers — the credential WALLET (Settings › LLM Providers).
 *
 * Layout (a card grid + two modals):
 *
 *   ┌─────────────────────────────────────────┐
 *   │  Your providers          [Update models] │
 *   │  ┌ provider card ┐ ┌ provider card ┐     │
 *   │  ┌ provider card ┐ ┌ + Add provider ┐    │
 *   └─────────────────────────────────────────┘
 *   • click a provider card → detail modal (models, masked key, endpoint,
 *     Test / Edit / Delete)
 *   • "+ Add provider" card → add modal with 2 methods: OAuth sign-in
 *     (Claude Code / Codex CLI) and a custom endpoint. The one-key preset
 *     is NOT here — first-run (WelcomePage's model step) already puts that
 *     exact card in front of every user, so a third tab repeating it was a
 *     duplicate of the path they arrived through.
 *
 * The GLOBAL DEFAULT model/framework does NOT live here anymore — it moved to
 * the "Model Defaults" nav section (ModelDefaultsSettings). Per-agent overrides
 * live in the chat page.
 *
 * Uses the bioluminescent terminal design system CSS variables.
 */

import { useState, useEffect, useCallback, useRef, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { RefreshCw, Plus, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { useConfigStore } from '@/stores'
import { Dialog, DialogContent, DialogFooter } from '@/components/ui'
import { api } from '@/lib/api'
import { OneKeyOnboard } from '@/components/settings/OneKeyOnboard'
import { SubscriptionConnect } from '@/components/settings/SubscriptionConnect'
import { useOauthAllowed } from '@/components/settings/useOauthAllowed'
import { addProviderCard, authFetch, providerApiUrl, type ProviderRow } from '@/lib/providersApi'
import {
  MODEL_SUGGESTION_GROUPS,
  type ModelSuggestionGroup,
} from '@/lib/agentFramework'

// =============================================================================
// Types
// =============================================================================

// The row shape lives in providersApi.ProviderRow — one definition for
// every consumer (this component used to declare its own copy).
type ProviderSummary = ProviderRow


// Preset quick-add lives in the shared OneKeyOnboard component (one-key
// setup via POST /api/providers/onboard) — the provider list, Get Key
// URLs, and recommended default models now live there / in
// model_catalog._ONBOARD_*_MODELS.

// MODEL_SUGGESTION_GROUPS (imported above) powers the custom-provider form's
// model bubble input. The framework/slot machinery that used to live here moved
// out with the global default → ModelDefaultsSettings.

// =============================================================================
// Model Bubble Tag Input
// =============================================================================

function ModelBubbleInput({
  models, onChange, placeholder, suggestions
}: {
  models: string[]
  onChange: (m: string[]) => void
  placeholder?: string
  suggestions?: ModelSuggestionGroup[]
}) {
  const { t } = useTranslation()
  const resolvedPlaceholder = placeholder ?? t('settings.provider.modelNamePlaceholder')
  const [input, setInput] = useState('')
  const hasPending = input.trim().length > 0
  const addModel = () => {
    const v = input.trim()
    if (v && !models.includes(v)) onChange([...models, v])
    setInput('')
  }
  const addSuggestion = (m: string) => {
    if (!models.includes(m)) onChange([...models, m])
  }
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        {models.map((m) => (
          <span key={m} className="inline-flex items-center gap-1.5 px-2 py-1 text-[12px] font-[family-name:var(--font-mono)] bg-[var(--bg-secondary)] text-[var(--text-primary)] border border-[var(--border-subtle)] whitespace-nowrap">
            {m}
            <button
              onClick={() => onChange(models.filter((x) => x !== m))}
              className="text-[var(--text-tertiary)] hover:text-[var(--color-error)] transition-colors"
              aria-label={t('settings.provider.removeModel', { model: m })}
            >
              ×
          </button>
        </span>
        ))}
        <span className="inline-flex items-center gap-1">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addModel() } }}
            placeholder={resolvedPlaceholder}
            style={{ width: Math.max(100, (input.length + 1) * 8) }}
            className={cn(
              'px-2 py-1 text-[12px] font-[family-name:var(--font-mono)] border bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none',
              hasPending
                ? 'border-[var(--color-warning)] focus:border-[var(--color-warning)]'
                : 'border-[var(--rule)] focus:border-[var(--text-primary)]'
            )}
          />
          <button
            onClick={addModel}
            disabled={!hasPending}
            className={cn(
              'px-2 py-1 text-[12px] font-[family-name:var(--font-mono)] border transition-all disabled:opacity-30',
              hasPending
                ? 'bg-[var(--text-primary)] text-[var(--text-inverse)] border-[var(--text-primary)] hover:opacity-90 animate-pulse'
                : 'border-[var(--rule)] text-[var(--text-tertiary)] hover:text-[var(--text-primary)]'
            )}
            aria-label={t('settings.provider.addModel')}
          >
            +
          </button>
        </span>
      </div>
      {hasPending && (
        <p className="text-xs text-[var(--color-warning)]">
          {t('settings.provider.pendingHint', { model: input.trim() })}
        </p>
      )}
      {suggestions && suggestions.length > 0 && (
        <ModelSuggestionChips
          groups={suggestions}
          selected={models}
          onPick={addSuggestion}
        />
      )}
    </div>
  )
}

function ModelSuggestionChips({
  groups, selected, onPick
}: {
  groups: ModelSuggestionGroup[]
  selected: string[]
  onPick: (m: string) => void
}) {
  const { t } = useTranslation()
  const visibleGroups = groups
    .map((g) => ({ ...g, models: g.models.filter((m) => !selected.includes(m)) }))
    .filter((g) => g.models.length > 0)
  if (visibleGroups.length === 0) return null
  return (
    <div className="pt-2 border-t border-[var(--border-subtle)] space-y-2">
      <p className="text-xs text-[var(--text-tertiary)]">
        {t('settings.provider.suggestionsHint')}
      </p>
      {visibleGroups.map((g) => (
        <div key={g.label} className="space-y-1">
          <span className="text-[10px] uppercase tracking-wider text-[var(--text-tertiary)] font-medium">
            {g.label}
          </span>
          <div className="flex flex-wrap gap-1.5">
            {g.models.map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => onPick(m)}
                className="inline-flex items-center gap-1 px-2.5 py-1 text-sm rounded-full border border-dashed border-[var(--border-default)] bg-[var(--bg-tertiary)]/50 text-[var(--text-tertiary)] opacity-70 hover:opacity-100 hover:bg-[var(--accent-primary)]/10 hover:text-[var(--accent-primary)] hover:border-[var(--accent-primary)]/50 transition-all whitespace-nowrap"
                title={t('settings.provider.addModelTitle', { model: m })}
              >
                <span className="text-[var(--text-tertiary)]">+</span>
                {m}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

// =============================================================================
// Section Header
// =============================================================================

function SectionHeader({ step, title, subtitle, action }: { step?: number; title: string; subtitle: string; action?: ReactNode }) {
  return (
    <div className="mb-4">
      <div className="flex items-baseline justify-between gap-3 mb-1">
        <div className="flex items-baseline gap-3 min-w-0">
          {step != null && (
            <span className="text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.18em] text-[var(--text-tertiary)] tabular-nums">
              {String(step).padStart(2, '0')}
            </span>
          )}
          <h3 className="text-base font-[family-name:var(--font-display)] font-semibold text-[var(--text-primary)] tracking-tight">
            {title}
          </h3>
        </div>
        {action && <div className="shrink-0 self-center">{action}</div>}
      </div>
      <p className={cn('text-sm text-[var(--text-tertiary)] leading-relaxed', step != null && 'ml-[44px]')}>{subtitle}</p>
    </div>
  )
}

// =============================================================================
// Main Component
// =============================================================================

// Security note (2026-06-17 → re-enabled 2026-07-09, Owner-authorized): custom
// endpoints are a first-class add method (the "Custom" tab). A user-supplied
// base_url routes the agent's LLM traffic to a host they choose — the tradeoff
// the original hardening flagged; kept visible here.

interface ProviderSettingsProps {
  /** Fired after every successful provider-list refresh. SetupPage uses
   * it to keep its footer ("Get Started" vs "Skip for now") live while
   * the Advanced disclosure stays open — before this, the count only
   * re-probed on collapse and a freshly connected subscription looked
   * like it hadn't taken (P0 2026-08-28). Held in a ref internally, so
   * any reference (inline arrows included) is safe. */
  onProvidersChanged?: () => void
  /** External refresh signal: bump the value to refetch the provider
   * list. SetupPage's subscription card lives OUTSIDE this component,
   * so a card added through it never passes refreshConfig — without
   * this signal the "Your providers" grid kept showing the stale list
   * until remount (Owner walkthrough, 2026-08-28). */
  refreshToken?: number
}

export function ProviderSettings({ onProvidersChanged, refreshToken }: ProviderSettingsProps = {}) {
  const { t } = useTranslation()
  const userId = useConfigStore((s) => s.userId)

  /** Build a provider API URL. Identity travels in headers (X-User-Id in
   * local, JWT in cloud) — not the query string. The previous version
   * appended `?user_id=...` which the backend used to fall back to when
   * the X-User-Id header was missing; that turned the URL into a second,
   * unsigned identity channel and made cross-user write/read bugs easy
   * to trigger. Backend now requires identity from headers only.
   *
   * The path is built by the shared providerApiUrl (which resolves the
   * backend host per invocation, so local/cloud switches always hit the
   * right host without a re-mount). */
  const providerUrl = useCallback((path: string = '') => providerApiUrl(path),
  // userId is intentionally a dependency: re-creating the callback on
  // user switch is cheap and forces all consumers (refreshConfig etc.)
  // to re-run under the new identity. The URL itself comes from the
  // shared builder; only the re-render semantics live here.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  [userId])

  const [providers, setProviders] = useState<Record<string, ProviderSummary>>({})
  const [error, setError] = useState('')

  const [syncing, setSyncing] = useState(false)
  // Inline summary line for the sync-defaults action: success / error / null.
  // Cleared whenever the user re-runs the sync so the UI never lies.
  const [syncResult, setSyncResult] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  // Protocol form
  const [showForm, setShowForm] = useState<'anthropic' | 'openai' | null>(null)
  const [formName, setFormName] = useState('')
  const [formUrl, setFormUrl] = useState('')
  const [formKey, setFormKey] = useState('')
  const [formAuth, setFormAuth] = useState<'api_key' | 'bearer_token'>('api_key')
  const [formModels, setFormModels] = useState<string[]>([])
  const [formAdding, setFormAdding] = useState(false)
  // In-form connectivity probe (verify BEFORE saving). Result is cleared
  // whenever the form context changes so the UI never shows a stale verdict.
  const [formTesting, setFormTesting] = useState(false)
  const [formTestResult, setFormTestResult] = useState<{ ok: boolean; msg: string } | null>(null)



  // Testing
  const [testing, setTesting] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; msg: string }>>({})


  // Edit-models dialog. We only support editing the models list (backend has
  // PUT /{id}/models) — name / url / key changes aren't exposed, so the
  // dialog deliberately only shows the ModelBubbleInput + suggestions.
  const [editingProviderId, setEditingProviderId] = useState<string | null>(null)
  const [editModels, setEditModels] = useState<string[]>([])
  const [editSaving, setEditSaving] = useState(false)
  const [editError, setEditError] = useState('')

  // Card-grid modals: the "+ Add provider" card opens the add modal (3 methods),
  // and clicking a provider card opens its detail modal.
  const [addModalOpen, setAddModalOpen] = useState(false)
  // Cloud non-staff may not add OAuth cards — hide the Sign-in tab
  // entirely (an entry point to a gated panel reads as "page broke").
  // null while probing / true elsewhere → tab visible (fail open; the
  // backend 403 is the real boundary). Deferred until the add modal
  // opens: the status route spawns a real `claude auth status`
  // subprocess on local — don't pay that for a closed modal.
  const oauthAllowed = useOauthAllowed(addModalOpen)
  const [detailProviderId, setDetailProviderId] = useState<string | null>(null)
  // Three ways to add. The one-key card (NetMind.AI Power / Anthropic /
  // OpenAI) is the same OneKeyOnboard first-run puts in front of every user;
  // it was dropped from Settings on 2026-09-03 as a duplicate and put back on
  // 2026-09-08 (Owner): a cloud user who skipped first-run, or wants to swap
  // the key later, otherwise finds only "Custom" here — the presets have no
  // other entry point once the welcome flow is behind them. It is the
  // default tab and never gated (the backend policy is the real boundary).
  const [addMethod, setAddMethod] = useState<'onekey' | 'oauth' | 'custom'>('onekey')
  // The Sign-in tab CAN disappear (cloud non-staff). Derive rather than
  // correct the state in an effect: an unavailable choice falls through to
  // 'custom' for both the tab highlight and the body, so the modal never opens
  // on a tab that renders nothing.
  const effectiveAddMethod = oauthAllowed === false && addMethod === 'oauth' ? 'custom' : addMethod

  // ---- Data loading ----
  // The callback lives in a ref so it stays OUT of refreshConfig's deps:
  // with it in the deps, any caller passing an inline arrow (the natural
  // React spelling) would re-create refreshConfig every render and turn
  // the mount effect below into an infinite refetch loop.
  const onProvidersChangedRef = useRef(onProvidersChanged)
  useEffect(() => { onProvidersChangedRef.current = onProvidersChanged })

  const refreshConfig = useCallback(async () => {
    try {
      const cfgRes = await authFetch(providerUrl()).then((r) => r.json())
      if (cfgRes.success) {
        setProviders(cfgRes.data.providers)
        onProvidersChangedRef.current?.()
      }
    } catch (err) {
      console.error('[ProviderSettings] refreshConfig failed:', err)
    }
  }, [providerUrl])

  // refreshToken in the deps: bumping it from outside refetches (see the
  // prop's doc); same-value re-renders are a no-op by effect semantics.
  useEffect(() => { refreshConfig() }, [refreshConfig, refreshToken])


  const providerList = Object.values(providers)
  const hasProviders = providerList.length > 0
  // The platform-funded card. Its presence is what makes the free-tier
  // explainer relevant — a bring-your-own-key user should not read it.
  const hasFreeTierCard = providerList.some((p) => p.source === 'netmind_free')

  // ---- Provider actions ----
  const addProvider = async (body: Record<string, unknown>) => {
    setError('')
    const res = await addProviderCard(body, t)
    if (!res.ok) { setError(res.error); return false }
    await refreshConfig()
    return true
  }


  const handleAddProtocol = async () => {
    if (!showForm || !formKey.trim()) { setError(t('settings.provider.enterApiKeyShort')); return }
    setFormAdding(true)
    const ok = await addProvider({
      card_type: showForm,
      name: formName.trim() || undefined,
      api_key: formKey.trim(),
      base_url: formUrl.trim(),
      auth_type: formAuth,
      models: formModels,
    })
    if (ok) {
      setShowForm(null); setFormName(''); setFormUrl(''); setFormKey(''); setFormAuth('api_key'); setFormModels([])
      setFormTestResult(null)
    }
    setFormAdding(false)
  }

  // Stateless "verify before save": probe the endpoint straight from the
  // current form values via /test-config — nothing is persisted, so the
  // user can fix a wrong key / url / model without polluting stored config.
  const handleTestForm = async () => {
    if (!showForm || !formKey.trim()) { setError(t('settings.provider.enterApiKeyShort')); return }
    setFormTesting(true)
    setFormTestResult(null)
    try {
      const res = await authFetch(providerUrl('/test-config'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          card_type: showForm,
          api_key: formKey.trim(),
          base_url: formUrl.trim(),
          auth_type: formAuth,
          models: formModels,
        }),
      }).then((r) => r.json()).catch(() => ({}))
      // On 401/422 the body is { detail }, not { success, message } — fall
      // back so we never render an empty red line (a blank "failure").
      const msg = res.message
        || (typeof res.detail === 'string' ? res.detail : null)
        || t('settings.provider.networkError')
      setFormTestResult({ ok: !!res.success, msg })
    } catch {
      setFormTestResult({ ok: false, msg: t('settings.provider.networkError') })
    }
    setFormTesting(false)
  }

  const handleDelete = async (id: string) => {
    await authFetch(providerUrl(`/${id}`), { method: 'DELETE' })
    await refreshConfig()
  }

  const handleSyncDefaults = async () => {
    if (!userId || syncing) return
    setSyncing(true)
    setSyncResult(null)
    try {
      const resp = await api.syncProviderDefaults()
      if (!resp.success) {
        setSyncResult({ kind: 'err', text: t('settings.provider.syncFailed') })
        return
      }
      if (resp.providers_updated === 0) {
        setSyncResult({ kind: 'ok', text: t('settings.provider.syncNothing') })
        return
      }
      const lines = resp.updates.map(u => `${u.name}: +${u.added.length} (${u.added.join(', ')})`)
      setSyncResult({
        kind: 'ok',
        text: `${t('settings.provider.syncUpdated', { providers: resp.providers_updated, models: resp.total_models_added })}\n${lines.join('\n')}`,
      })
      await refreshConfig()
    } catch (e) {
      setSyncResult({ kind: 'err', text: t('settings.provider.syncFailedError', { error: e instanceof Error ? e.message : String(e) }) })
    } finally {
      setSyncing(false)
    }
  }

  const openEditModels = (prov: ProviderSummary) => {
    setEditingProviderId(prov.provider_id)
    setEditModels([...prov.models])
    setEditError('')
  }
  const closeEditModels = () => {
    setEditingProviderId(null)
    setEditModels([])
    setEditError('')
  }
  const saveEditModels = async () => {
    if (!editingProviderId) return
    setEditSaving(true)
    setEditError('')
    try {
      const res = await authFetch(providerUrl(`/${editingProviderId}/models`), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ models: editModels }),
      }).then((r) => r.json())
      if (res.success) {
        await refreshConfig()
        closeEditModels()
      } else {
        setEditError(res.detail || t('settings.provider.updateModelsFailed'))
      }
    } catch {
      setEditError(t('settings.provider.networkError'))
    }
    setEditSaving(false)
  }

  const handleTest = async (id: string) => {
    setTesting(id)
    try {
      const res = await authFetch(providerUrl(`/${id}/test`), { method: 'POST' }).then((r) => r.json())
      setTestResults((p) => ({ ...p, [id]: { ok: res.success, msg: res.message } }))
    } catch {
      setTestResults((p) => ({ ...p, [id]: { ok: false, msg: t('settings.provider.networkError') } }))
    }
    setTesting(null)
  }

  // Local slot change. Preserves the slot's reasoning params: switching
  // provider/model must not silently reset Thinking/Reasoning Effort.

  const openForm = (protocol: 'anthropic' | 'openai') => {
    setShowForm(protocol)
    setFormName('')
    setFormUrl(protocol === 'anthropic' ? 'https://api.anthropic.com' : 'https://api.openai.com/v1')
    setFormKey(''); setFormAuth('api_key'); setFormModels([]); setError('')
    setFormTesting(false); setFormTestResult(null)
  }

  // ---- Full view (always expanded) ----
  return (
    <div className="space-y-8">

      {/* ================================================================= */}
      {/* ① Your providers — the configured list, at the TOP. Claude Code    */}
      {/*    Login / Codex CLI Login are provider types too: they show here   */}
      {/*    once added, and as sign-in options in "Add a provider" below.    */}
      {/* ================================================================= */}
      <div>
        <SectionHeader
          title={t('settings.provider.providersListTitle')}
          subtitle={t('settings.provider.providersListSubtitle')}
          action={
            hasProviders ? (
              // "Update available models" — maintenance on the existing
              // providers (backfills the latest default model lists). A header
              // action, not an add step: hover → what it does, click → run it.
              <TooltipProvider delayDuration={200}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      onClick={handleSyncDefaults}
                      disabled={syncing || !userId}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-[var(--radius-lg)] border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--nm-paper-warm)] disabled:opacity-40 transition-colors"
                    >
                      <RefreshCw className={cn('w-3.5 h-3.5', syncing && 'animate-spin')} />
                      {syncing ? t('settings.provider.syncing') : t('settings.provider.updateModelsBtn')}
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="left" className="max-w-[280px]">
                    {t('settings.provider.updateModelsDesc')}
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            ) : undefined
          }
        />
        {/* Free-tier explainer. Users kept asking two things the card grid
            cannot answer on its own: how do I actually start using my own key,
            and do I have to burn the free credit first (no). Placed above the
            grid so it is read before someone goes looking for a Delete button
            on the free card — which is exactly what we block. */}
        {hasFreeTierCard && (
          <div className="mb-4 rounded-[var(--radius-xl)] border border-[var(--border-default)] bg-[var(--bg-tertiary)]/40 px-4 py-3 text-xs leading-relaxed text-[var(--text-secondary)] space-y-1.5">
            <div className="font-medium text-[var(--text-primary)]">
              {t('settings.provider.freeTierHowToTitle')}
            </div>
            <div>{t('settings.provider.freeTierHowToSwitch')}</div>
            <div>{t('settings.provider.freeTierAnytime')}</div>
            <div className="text-[var(--text-tertiary)]">
              {t('settings.provider.freeTierNoDelete')}
            </div>
          </div>
        )}
        {syncResult && (
          <p
            className={cn(
              'text-xs whitespace-pre-wrap leading-relaxed mb-3 ml-[34px] -mt-2',
              syncResult.kind === 'ok'
                ? 'text-[var(--text-secondary)]'
                : 'text-[var(--color-error)]'
            )}
          >
            {syncResult.text}
          </p>
        )}
        <div className="ml-[34px]">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {providerList.map((prov) => (
              <button
                key={prov.provider_id}
                type="button"
                onClick={() => setDetailProviderId(prov.provider_id)}
                className="text-left p-4 rounded-[var(--radius-xl)] border border-[var(--border-subtle)] bg-[var(--bg-primary)] hover:border-[var(--accent-primary)]/40 transition-colors"
              >
                <div className="flex items-center justify-between gap-2 mb-1">
                  <span className="text-sm font-medium text-[var(--text-primary)] truncate">{prov.name}</span>
                  <span className="text-[10px] px-2 py-0.5 rounded bg-[var(--bg-tertiary)] text-[var(--text-tertiary)] uppercase shrink-0">{prov.protocol}</span>
                </div>
                <div className="text-xs text-[var(--text-tertiary)] truncate">
                  {prov.api_key_masked || prov.source} · {t('settings.provider.modelsCount', { count: prov.models.length })}
                </div>
              </button>
            ))}
            {/* + Add provider card — opens the add modal on its first tab. */}
            <button
              type="button"
              onClick={() => { setAddMethod('onekey'); setAddModalOpen(true) }}
              className="flex flex-col items-center justify-center gap-1 p-4 rounded-[var(--radius-xl)] border border-dashed border-[var(--border-default)] text-[var(--text-tertiary)] hover:border-[var(--accent-primary)]/50 hover:text-[var(--text-secondary)] transition-colors min-h-[76px]"
            >
              <Plus className="w-5 h-5" />
              <span className="text-sm">{t('settings.provider.addProviderTitle')}</span>
            </button>
          </div>
        </div>
      </div>

      {/* ================================================================= */}
      {/* ② Add a provider — a modal opened from the "+ Add provider" grid card.
          Three methods: the one-key presets (same card as first-run), OAuth
          sign-in (Claude Code / Codex CLI) and a custom endpoint. */}
      <Dialog
        isOpen={addModalOpen}
        onClose={() => setAddModalOpen(false)}
        title={t('settings.provider.addProviderTitle')}
        size="2xl"
      >
        <DialogContent>
          {/* Tabs — three ways to add, switched in place (no wizard menu). */}
          <div className="flex gap-1 border-b border-[var(--border-subtle)] mb-4">
            {([
              { id: 'onekey', label: t('settings.provider.tabOneKey') },
              // Dropped for cloud non-staff (oauthAllowed === false) — an
              // entry point to a gated panel reads as "page broke". With the
              // tab gone the default falls through to 'custom' above.
              ...(oauthAllowed === false
                ? []
                : [{ id: 'oauth', label: t('settings.provider.tabSignin') }]),
              { id: 'custom', label: t('settings.provider.tabCustom') },
            ] as Array<{ id: 'onekey' | 'oauth' | 'custom'; label: string }>).map((tb) => (
              <button
                key={tb.id}
                type="button"
                onClick={() => setAddMethod(tb.id)}
                className={cn(
                  'px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
                  effectiveAddMethod === tb.id
                    ? 'border-[var(--accent-primary)] text-[var(--text-primary)]'
                    : 'border-transparent text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]'
                )}
              >
                {tb.label}
              </button>
            ))}
          </div>
          <div className="space-y-4">
          {effectiveAddMethod === 'onekey' && (
            <OneKeyOnboard
              hideHeader
              bare
              onComplete={() => {
                setAddModalOpen(false)
                void refreshConfig()
              }}
            />
          )}
          {effectiveAddMethod === 'oauth' && (
            <SubscriptionConnect
              providers={providerList}
              addProvider={addProvider}
            />
          )}

          {effectiveAddMethod === 'custom' && (
          <div className="space-y-4">
            {/* Step 1: pick the protocol; the fields only appear after that. */}
            <div>
              <label className="block text-sm text-[var(--text-tertiary)] mb-1">{t('settings.provider.protocolLabel')}</label>
              <select
                value={showForm || ''}
                onChange={(e) => {
                  const v = e.target.value
                  if (!v) setShowForm(null)
                  else openForm(v as 'anthropic' | 'openai')
                }}
                className="w-full px-3 py-2 text-sm rounded-[var(--radius-lg)] border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
              >
                <option value="">{t('settings.provider.selectProtocol')}</option>
                <option value="openai">{t('settings.provider.protocolOpenai')}</option>
                <option value="anthropic">{t('settings.provider.protocolAnthropic')}</option>
              </select>
            </div>

            {/* Step 2: the endpoint fields (shown once a protocol is chosen). */}
            {showForm && (
              <div className="p-4 rounded-[var(--radius-xl)] border border-[var(--border-default)] bg-[var(--bg-tertiary)] space-y-3">
                <p className="text-sm text-[var(--text-tertiary)]">
                  {showForm === 'anthropic' ? t('settings.provider.anthropicEndpointHint') : t('settings.provider.openaiEndpointHint')}
                </p>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-sm text-[var(--text-tertiary)] mb-1">{t('settings.provider.providerNameLabel')}</label>
                    <input type="text" value={formName} onChange={(e) => setFormName(e.target.value)}
                      placeholder={showForm === 'anthropic' ? t('settings.provider.providerNameEgAnthropic') : t('settings.provider.providerNameEgOpenai')}
                      className="w-full px-3 py-2 text-sm rounded-[var(--radius-lg)] border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
                  </div>
                  {showForm === 'anthropic' ? (
                    <div>
                      <label className="block text-sm text-[var(--text-tertiary)] mb-1">{t('settings.provider.authType')}</label>
                      <select value={formAuth} onChange={(e) => { setFormAuth(e.target.value as 'api_key' | 'bearer_token'); setFormTestResult(null) }}
                        className="w-full px-3 py-2 text-sm rounded-[var(--radius-lg)] border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none">
                        <option value="api_key">{t('settings.provider.authApiKey')}</option>
                        <option value="bearer_token">{t('settings.provider.authBearerToken')}</option>
                      </select>
                    </div>
                  ) : <div />}
                </div>
                <div>
                  <label className="block text-sm text-[var(--text-tertiary)] mb-1">{t('settings.provider.baseUrl')}</label>
                  <input type="text" value={formUrl} onChange={(e) => { setFormUrl(e.target.value); setFormTestResult(null) }}
                    placeholder={t('settings.provider.baseUrl')}
                    className="w-full px-3 py-2 text-sm rounded-[var(--radius-lg)] border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
                </div>
                <div>
                  <label className="block text-sm text-[var(--text-tertiary)] mb-1">{t('settings.provider.apiKeyLabel')}</label>
                  <input type="password" value={formKey} onChange={(e) => { setFormKey(e.target.value); setFormTestResult(null) }}
                    placeholder={t('settings.provider.yourApiKey')}
                    className="w-full px-3 py-2 text-sm rounded-[var(--radius-lg)] border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
                </div>
                <div>
                  <label className="block text-sm text-[var(--text-tertiary)] mb-1">{t('settings.provider.availableModels')}</label>
                  <ModelBubbleInput
                    models={formModels}
                    onChange={(m) => { setFormModels(m); setFormTestResult(null) }}
                    suggestions={MODEL_SUGGESTION_GROUPS}
                  />
                </div>
                {formTestResult && (
                  <p className={cn('text-sm', formTestResult.ok ? 'text-[var(--color-success)]' : 'text-[var(--color-error)]')}>
                    {formTestResult.msg}
                  </p>
                )}
                <div className="flex gap-2">
                  <button onClick={handleTestForm} disabled={formTesting || formAdding || !formKey.trim()}
                    className="inline-flex items-center justify-center px-4 py-2.5 text-sm font-medium rounded-[var(--radius-lg)] border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--nm-paper-warm)] disabled:opacity-40 transition-colors">
                    {formTesting ? (
                      <>
                        <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                        {t('settings.provider.testingConnection')}
                      </>
                    ) : (
                      t('settings.provider.testConnection')
                    )}
                  </button>
                  <button onClick={handleAddProtocol} disabled={formAdding || !formKey.trim()}
                    className="flex-1 py-2.5 text-sm font-medium rounded-[var(--radius-lg)] bg-[var(--text-primary)] text-[var(--text-inverse)] hover:opacity-90 disabled:opacity-40 transition-colors">
                    {formAdding ? t('settings.provider.adding') : t('settings.provider.addProvider')}
                  </button>
                </div>
              </div>
            )}
          </div>
          )}

          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          </div>
        </DialogContent>
      </Dialog>

      {/* Provider detail modal — opened by clicking a provider card. Shows the
          provider's models + masked key + endpoint, plus Test / Edit / Delete
          (reusing the existing handlers). */}
      {(() => {
        const prov = detailProviderId ? providers[detailProviderId] : null
        if (!prov) return null
        return (
          <Dialog isOpen={!!prov} onClose={() => setDetailProviderId(null)} title={prov.name} size="xl">
            <DialogContent>
              <div className="space-y-3 text-sm">
                <div className="flex flex-wrap gap-2">
                  <span className="text-xs px-2 py-0.5 rounded bg-[var(--bg-tertiary)] text-[var(--text-tertiary)] uppercase">{prov.protocol}</span>
                  <span className="text-xs px-2 py-0.5 rounded bg-[var(--bg-tertiary)] text-[var(--text-tertiary)]">{prov.source}</span>
                  <span className="text-xs px-2 py-0.5 rounded bg-[var(--bg-tertiary)] text-[var(--text-tertiary)]">{prov.auth_type}</span>
                </div>
                {prov.base_url && (
                  <div>
                    <span className="text-[var(--text-tertiary)]">
                      {t('settings.provider.endpointLabel')}:{' '}
                    </span>
                    <span className="font-mono text-xs break-all">{prov.base_url}</span>
                  </div>
                )}
                <div>
                  <span className="text-[var(--text-tertiary)]">
                    {t('settings.provider.apiKeyLabel')}:{' '}
                  </span>
                  <span className="font-mono text-xs">{prov.api_key_masked || '—'}</span>
                </div>
                {prov.netmind_account_email && (
                  <div>
                    <span className="text-[var(--text-tertiary)]">
                      {t('settings.provider.netmindAccountLabel')}:{' '}
                    </span>
                    <span className="font-mono text-xs break-all">{prov.netmind_account_email}</span>
                  </div>
                )}
                <div>
                  <div className="text-[var(--text-tertiary)] mb-1">{t('settings.provider.modelsCount', { count: prov.models.length })}</div>
                  <div className="flex flex-wrap gap-1.5">
                    {prov.models.map((m) => (
                      <span key={m} className="text-xs px-2 py-0.5 rounded bg-[var(--bg-tertiary)] text-[var(--text-secondary)] font-mono">{m}</span>
                    ))}
                  </div>
                </div>
                {testResults[prov.provider_id] && (
                  <p className={cn('text-sm', testResults[prov.provider_id].ok ? 'text-[var(--color-success)]' : 'text-[var(--color-error)]')}>
                    {testResults[prov.provider_id].msg}
                  </p>
                )}
              </div>
            </DialogContent>
            <DialogFooter>
              {/* OAuth cards run a REAL CLI one-shot on Test (5-15s) — a
                  static "..." read as frozen (Owner walkthrough). Spinner +
                  label, same pattern as OneKeyOnboard's submit. */}
              <button onClick={() => handleTest(prov.provider_id)} disabled={testing === prov.provider_id}
                className="inline-flex items-center px-4 py-2 text-sm rounded-[var(--radius-lg)] text-[var(--accent-primary)] hover:bg-[var(--accent-primary)]/5 disabled:opacity-40 transition-colors">
                {testing === prov.provider_id ? (
                  <>
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    {t('settings.provider.testingConnection')}
                  </>
                ) : (
                  t('settings.provider.test')
                )}
              </button>
              {/* OAuth cards' model lists are code-owned (codex: curated
                  constant; claude: CLI family aliases) — the backend overrides
                  the stored column at read time, so editing here would be a
                  silent no-op. */}
              {prov.source !== 'claude_oauth' && prov.source !== 'codex_oauth' && (
                <button onClick={() => openEditModels(prov)}
                  className="px-4 py-2 text-sm rounded-[var(--radius-lg)] border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--nm-paper-warm)] transition-colors">
                  {t('settings.provider.edit')}
                </button>
              )}
              <button onClick={() => { handleDelete(prov.provider_id); setDetailProviderId(null) }}
                className="px-4 py-2 text-sm rounded-[var(--radius-lg)] text-[var(--color-error)] hover:bg-[var(--color-error)]/5 transition-colors">
                {t('settings.provider.delete')}
              </button>
            </DialogFooter>
          </Dialog>
        )
      })()}

      {/* ================================================================= */}
      {/* Edit-models dialog                                                 */}
      {/* ================================================================= */}
      {(() => {
        const prov = editingProviderId ? providers[editingProviderId] : null
        if (!prov) return null
        return (
          <Dialog
            isOpen={!!prov}
            onClose={editSaving ? () => { /* block close while saving */ } : closeEditModels}
            title={t('settings.provider.editModelsTitle', { name: prov.name })}
            size="2xl"
          >
            <DialogContent>
              <p className="text-sm text-[var(--text-tertiary)] mb-3">
                {t('settings.provider.editModelsHint')}
              </p>
              <ModelBubbleInput
                models={editModels}
                onChange={setEditModels}
                suggestions={MODEL_SUGGESTION_GROUPS}
              />
              {editError && (
                <p className="mt-3 text-sm text-[var(--color-error)]">{editError}</p>
              )}
            </DialogContent>
            <DialogFooter>
              <button
                onClick={closeEditModels}
                disabled={editSaving}
                className="px-4 py-2 text-sm rounded-[var(--radius-lg)] border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--nm-paper-warm)] disabled:opacity-40 transition-colors"
              >
                {t('settings.provider.cancel')}
              </button>
              <button
                onClick={saveEditModels}
                disabled={editSaving}
                className="px-4 py-2 text-sm font-medium rounded-[var(--radius-lg)] bg-[var(--accent-primary)] text-[var(--text-inverse)] hover:bg-[var(--accent-primary)]/90 disabled:opacity-40 transition-colors"
              >
                {editSaving ? t('settings.provider.saving') : t('settings.provider.save')}
              </button>
            </DialogFooter>
          </Dialog>
        )
      })()}

    </div>
  )
}
