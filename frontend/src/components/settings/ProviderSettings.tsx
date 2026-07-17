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
 *   • "+ Add provider" card → add modal with 3 methods: OAuth sign-in
 *     (Claude Code / Codex CLI), one-key preset, custom endpoint.
 *
 * The GLOBAL DEFAULT model/framework does NOT live here anymore — it moved to
 * the "Model Defaults" nav section (ModelDefaultsSettings). Per-agent overrides
 * live in the chat page.
 *
 * Uses the bioluminescent terminal design system CSS variables.
 */

import { useState, useEffect, useCallback, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { RefreshCw, Plus } from 'lucide-react'
import { cn } from '@/lib/utils'
import { OneKeyOnboard } from './OneKeyOnboard'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { useConfigStore } from '@/stores'
import { getApiBaseUrl } from '@/stores/runtimeStore'
import { Dialog, DialogContent, DialogFooter } from '@/components/ui'
import { api } from '@/lib/api'
import { isTauri, triggerClaudeLogin, triggerClaudeLogout, cancelClaudeLogin } from '@/lib/tauri'
import {
  MODEL_SUGGESTION_GROUPS,
  type ModelSuggestionGroup,
} from '@/lib/agentFramework'

/** How long we let `claude auth login` block before auto-aborting it.
 *  Anthropic's OAuth flow itself has no hard upper bound, but past ~10 min
 *  the user has almost certainly closed the browser tab and the CLI is
 *  just sitting on a dead callback server. Keeping it as a constant so
 *  the value is visible in one place + cheap to tune. */
const CLAUDE_LOGIN_TIMEOUT_SEC = 600

/** fetch wrapper that injects the identity headers configStore tracks.
 *
 * Two headers, mutually compatible (mirror of ApiClient.getAuthHeaders):
 *   - Authorization: Bearer <jwt>  — cloud mode signed identity
 *   - X-User-Id: <user_id>         — local mode unsigned identity
 *
 * Sending both is intentional. Backend auth_middleware picks the right
 * one for the active mode and ignores the other (defence in depth: a
 * cloud server won't honour X-User-Id even if a client sets it).
 *
 * History: until 2026-05-18 this wrapper only sent the JWT, which
 * silently broke local mode. Settings calls landed under whatever user
 * the backend's "first row in users" fallback resolved to (the eldest
 * account), so a freshly-registered user's API key + slot bindings got
 * written to someone else's row. Now we always send X-User-Id and the
 * backend has lost the dangerous fallback — see auth.py 2026-05-18 note. */
function authFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers)
  try {
    const raw = localStorage.getItem('narra-nexus-config')
    if (raw) {
      const state = JSON.parse(raw)?.state || {}
      if (state.token) headers.set('Authorization', `Bearer ${state.token}`)
      if (state.userId) headers.set('X-User-Id', state.userId)
    }
  } catch {
    // Corrupt/absent localStorage config — proceed without auth headers;
    // the backend 401s if the request actually needed them.
  }
  return fetch(input, { ...init, headers })
}

// =============================================================================
// Types
// =============================================================================

interface ProviderSummary {
  provider_id: string
  name: string
  source: string
  protocol: string
  auth_type: string
  is_active: boolean
  models: string[]
  api_key_masked?: string
  base_url?: string
  // NetMind account this key belongs to (captured at mint). Lets the user tell
  // several keys from one broke account apart and top up the right one.
  netmind_account_email?: string
}


// Preset quick-add moved to the shared OneKeyOnboard component (one-key
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
              className="text-[var(--text-tertiary)] hover:text-[var(--color-red-500)] transition-colors"
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
// Helpers
// =============================================================================

/** "9:32" / "0:08" — countdown formatter for the login timeout label. */
function formatCountdown(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds))
  const m = Math.floor(s / 60)
  const sec = s % 60
  return `${m}:${sec.toString().padStart(2, '0')}`
}

/** Best-effort render of whatever expiry value the CLI handed us.
 *
 * The Claude Code CLI shifts schemas across minor versions: some builds
 * emit ISO-8601 strings, others emit unix epoch (sec OR ms). We accept
 * any of them. If parsing fails we just show the raw value rather than
 * eating the field — the user still gets *something* useful. */
function formatExpiresAt(raw: string | null | undefined): string | null {
  if (!raw) return null
  const trimmed = String(raw).trim()
  if (!trimmed) return null
  const n = Number(trimmed)
  let d: Date | null = null
  if (Number.isFinite(n) && n > 0) {
    d = new Date(n < 1e12 ? n * 1000 : n)
  } else {
    const t = Date.parse(trimmed)
    if (!Number.isNaN(t)) d = new Date(t)
  }
  if (!d || Number.isNaN(d.getTime())) return trimmed
  return d.toLocaleString()
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

export function ProviderSettings() {
  const { t } = useTranslation()
  const userId = useConfigStore((s) => s.userId)

  /** Build a provider API URL. Identity travels in headers (X-User-Id in
   * local, JWT in cloud) — not the query string. The previous version
   * appended `?user_id=...` which the backend used to fall back to when
   * the X-User-Id header was missing; that turned the URL into a second,
   * unsigned identity channel and made cross-user write/read bugs easy
   * to trigger. Backend now requires identity from headers only.
   *
   * IMPORTANT: getApiBaseUrl() is called INSIDE the callback (not captured at
   * component mount), so it always reflects the current mode. When the user
   * switches between local and cloud, every fresh call returns the right host
   * without needing to re-mount this component. */
  const providerUrl = useCallback((path: string = '') => {
    return `${getApiBaseUrl()}/api/providers${path}`
  // userId is intentionally a dependency: re-creating the callback on
  // user switch is cheap and forces all consumers (refreshConfig etc.)
  // to re-run under the new identity.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId])

  const [providers, setProviders] = useState<Record<string, ProviderSummary>>({})
  const [error, setError] = useState('')
  // ``allowed`` is false only when the backend gated this caller out:
  // cloud mode + non-staff. Staff and local mode omit it (→ undefined →
  // allowed). Same field on codexStatus; both routes apply the identical
  // ``is_cloud and not is_staff`` gate, so they always agree.
  const [claudeStatus, setClaudeStatus] = useState<{ cli_installed: boolean; logged_in: boolean; email: string | null; expires_at: string | null; allowed?: boolean } | null>(null)
  const [claudeLoggingIn, setClaudeLoggingIn] = useState(false)
  const [claudeLoggingOut, setClaudeLoggingOut] = useState(false)
  // Codex CLI Login — parallel to Claude Code Login. Same shape. In
  // local mode the backend auto-installs `@openai/codex` when the
  // user opts into the codex_cli agent framework, but `codex login`
  // (OAuth) is still a manual terminal step because it opens a
  // browser.
  const [codexStatus, setCodexStatus] = useState<{ cli_installed: boolean; logged_in: boolean; email: string | null; expires_at: string | null; allowed?: boolean } | null>(null)
  // Seconds remaining on the login auto-abort timer, or null when no
  // login is in flight. Decremented every 1s by the effect below; on
  // hitting 0 we fire cancelClaudeLogin so the Rust side SIGTERMs the
  // dangling `claude auth login` child.
  const [claudeLoginRemaining, setClaudeLoginRemaining] = useState<number | null>(null)

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
  const [detailProviderId, setDetailProviderId] = useState<string | null>(null)
  // Add-provider modal is a two-step wizard: 'menu' shows the three methods,
  // then the chosen one fills the modal (with a back link). Avoids the old
  // "everything stacked at once" wall — especially the custom form.
  const [addMethod, setAddMethod] = useState<'onekey' | 'oauth' | 'custom'>('onekey')

  // ---- Data loading ----
  const refreshConfig = useCallback(async () => {
    try {
      const [cfgRes, claudeRes, codexRes] = await Promise.all([
        authFetch(providerUrl()).then((r) => r.json()),
        authFetch(providerUrl('/claude-status')).then((r) => r.json()).catch(() => null),
        authFetch(providerUrl('/codex-status')).then((r) => r.json()).catch(() => null),
      ])
      if (claudeRes?.success) setClaudeStatus(claudeRes.data)
      if (codexRes?.success) setCodexStatus(codexRes.data)
      if (cfgRes.success) {
        setProviders(cfgRes.data.providers)
      }
    } catch (err) {
      console.error('[ProviderSettings] refreshConfig failed:', err)
    }
  }, [providerUrl])

  useEffect(() => { refreshConfig() }, [refreshConfig])

  // Login auto-abort timer. Set claudeLoginRemaining to N to start
  // counting down to 0; reaching 0 fires cancelClaudeLogin which
  // SIGTERMs the dangling `claude auth login` child on the Rust side.
  // Setting it to null (e.g. on natural completion) clears the timer.
  useEffect(() => {
    if (claudeLoginRemaining === null) return
    if (claudeLoginRemaining <= 0) {
      cancelClaudeLogin().catch((e) => console.error('cancelClaudeLogin failed:', e))
      // Don't null it here — handleClaudeLogin's finally clears state
      // once the trigger's await resolves with the SIGTERM exit code.
      // Returning early prevents a re-fire next tick.
      return
    }
    const t = setTimeout(
      () => setClaudeLoginRemaining((r) => (r === null ? null : r - 1)),
      1000,
    )
    return () => clearTimeout(t)
  }, [claudeLoginRemaining])

  const providerList = Object.values(providers)
  const hasProviders = providerList.length > 0
  const hasClaude = providerList.some((p) => p.source === 'claude_oauth')
  const hasCodex = providerList.some((p) => p.source === 'codex_oauth')


  // ---- Provider actions ----
  const addProvider = async (body: Record<string, unknown>) => {
    setError('')
    try {
      const res = await authFetch(providerUrl(), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }).then((r) => r.json())
      if (!res.success) { setError(res.detail || t('settings.provider.failed')); return false }
      await refreshConfig()
      return true
    } catch { setError(t('settings.provider.networkError')); return false }
  }

  const handleAddClaudeOAuth = async () => {
    await addProvider({ card_type: 'claude_oauth' })
  }

  const handleAddCodexOAuth = async () => {
    await addProvider({ card_type: 'codex_oauth' })
  }

  const handleClaudeLogin = async () => {
    setClaudeLoggingIn(true)
    setClaudeLoginRemaining(CLAUDE_LOGIN_TIMEOUT_SEC)
    try {
      await triggerClaudeLogin()
      // After login completes, refresh to pick up the new status
      await refreshConfig()
    } catch (e) {
      // SIGTERM from the timeout path also lands here (claude exits
      // non-zero). The finally below resets state regardless.
      console.error('Claude login failed:', e)
    } finally {
      setClaudeLoggingIn(false)
      setClaudeLoginRemaining(null)
    }
  }

  const handleClaudeLogout = async () => {
    setClaudeLoggingOut(true)
    try {
      await triggerClaudeLogout()
      await refreshConfig()
    } catch (e) {
      console.error('Claude logout failed:', e)
    } finally {
      setClaudeLoggingOut(false)
    }
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
    }
    setFormAdding(false)
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
                      className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-lg border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] disabled:opacity-40 transition-colors"
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
        {syncResult && (
          <p
            className={cn(
              'text-xs whitespace-pre-wrap leading-relaxed mb-3 ml-[34px] -mt-2',
              syncResult.kind === 'ok'
                ? 'text-[var(--text-secondary)]'
                : 'text-[var(--color-red-500)]'
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
                className="text-left p-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-primary)] hover:border-[var(--accent-primary)]/40 transition-colors"
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
            {/* + Add provider card — opens the 3-method add modal. */}
            <button
              type="button"
              onClick={() => { setAddMethod('onekey'); setAddModalOpen(true) }}
              className="flex flex-col items-center justify-center gap-1 p-4 rounded-xl border border-dashed border-[var(--border-default)] text-[var(--text-tertiary)] hover:border-[var(--accent-primary)]/50 hover:text-[var(--text-secondary)] transition-colors min-h-[76px]"
            >
              <Plus className="w-5 h-5" />
              <span className="text-sm">{t('settings.provider.addProviderTitle')}</span>
            </button>
          </div>
        </div>
      </div>

      {/* ================================================================= */}
      {/* ② Add a provider — a modal opened from the "+ Add provider" grid card.
          Three methods: OAuth sign-in (Claude Code / Codex CLI), the one-key
          preset, and a custom endpoint. */}
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
              { id: 'onekey', label: t('settings.provider.tabApiKey') },
              { id: 'oauth', label: t('settings.provider.tabSignin') },
              { id: 'custom', label: t('settings.provider.tabCustom') },
            ] as const).map((tb) => (
              <button
                key={tb.id}
                type="button"
                onClick={() => setAddMethod(tb.id)}
                className={cn(
                  'px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
                  addMethod === tb.id
                    ? 'border-[var(--accent-primary)] text-[var(--text-primary)]'
                    : 'border-transparent text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]'
                )}
              >
                {tb.label}
              </button>
            ))}
          </div>
          <div className="space-y-4">
          {/* API key — one-key preset dropdown + paste key. */}
          {addMethod === 'onekey' && <OneKeyOnboard onComplete={refreshConfig} />}

          {addMethod === 'oauth' && (<>
          {/* ---- Claude Code Login Card ----
            *
            * The card surfaces TWO independent pieces of state and lets the
            * user act on each separately:
            *
            *   1. OS credential state \u2014 owned by the `claude` CLI and
            *      stored in `~/.claude/.credentials.json`. Drives
            *      Login / Re-login / Logout buttons.
            *
            *   2. Provider record state \u2014 owned by NarraNexus and stored
            *      in `user_providers`. Drives the "Add as Provider" /
            *      "Remove" affordance.
            *
            * Earlier versions hid the entire login UI once `hasClaude`
            * was true, which prevented account switching, re-auth after
            * token expiry, and viewing the active account. Decoupling
            * the two layers means a user can re-login, switch accounts,
            * or sign out without first having to delete the provider.
            */}
          <div className="p-4 rounded-xl border border-[var(--accent-primary)]/20 bg-[var(--accent-primary)]/5">
            <div className="flex items-center gap-2 mb-1">
              <h4 className="text-sm font-medium text-[var(--text-primary)]">Claude Code Login</h4>
            </div>
            <p className="text-sm text-[var(--text-tertiary)] mb-3">{t('settings.provider.claudeOauthDesc')}</p>

            {!claudeStatus && (
              <p className="text-sm text-[var(--text-tertiary)]">{t('settings.provider.checkingStatus')}</p>
            )}

            {claudeStatus && (
              <div className="space-y-3">
                {/* ---- Section A: OS credential state ---- */}
                <div className="space-y-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={cn('inline-block w-2 h-2 rounded-full',
                      claudeStatus.logged_in ? 'bg-[var(--color-success)]' :
                      claudeStatus.cli_installed ? 'bg-[var(--color-warning)]' : 'bg-[var(--text-tertiary)]'
                    )} />
                    <span className="text-sm text-[var(--text-secondary)]">
                      {claudeStatus.logged_in
                        ? <>{t('settings.provider.loggedIn')}{claudeStatus.email ? <> {t('settings.provider.loggedInAs')} <span className="font-mono">{claudeStatus.email}</span></> : null}</>
                        : claudeStatus.cli_installed ? t('settings.provider.notLoggedIn') : t('settings.provider.cliNotInstalled')}
                    </span>
                    {claudeStatus.logged_in && claudeStatus.expires_at && (
                      <span className="text-xs text-[var(--text-tertiary)]">
                        {t('settings.provider.expires', { date: formatExpiresAt(claudeStatus.expires_at) })}
                      </span>
                    )}
                  </div>

                  {/* Action buttons. Always visible when CLI is installed
                    * + Tauri \u2014 never hidden behind a provider-record check. */}
                  {claudeStatus.cli_installed && isTauri() && (
                    <div className="flex gap-2 flex-wrap">
                      {claudeStatus.logged_in ? (
                        <>
                          <button onClick={handleClaudeLogin}
                            disabled={claudeLoggingIn || claudeLoggingOut}
                            className="px-4 py-2 text-sm font-medium rounded-lg border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] disabled:opacity-50 transition-colors">
                            {claudeLoggingIn
                              ? (claudeLoginRemaining !== null
                                  ? t('settings.provider.reLoggingInCountdown', { time: formatCountdown(claudeLoginRemaining) })
                                  : t('settings.provider.reLoggingIn'))
                              : t('settings.provider.reLogin')}
                          </button>
                          <button onClick={handleClaudeLogout}
                            disabled={claudeLoggingIn || claudeLoggingOut}
                            className="px-4 py-2 text-sm font-medium rounded-lg border border-[var(--color-error)]/30 text-[var(--color-error)] hover:bg-[var(--color-error)]/5 disabled:opacity-50 transition-colors">
                            {claudeLoggingOut ? t('settings.provider.loggingOut') : t('settings.provider.logout')}
                          </button>
                        </>
                      ) : (
                        <button onClick={handleClaudeLogin}
                          disabled={claudeLoggingIn}
                          className="px-4 py-2 text-sm font-medium rounded-lg bg-[var(--accent-primary)] text-[var(--text-inverse)] hover:opacity-90 transition-colors disabled:opacity-50">
                          {claudeLoggingIn
                            ? (claudeLoginRemaining !== null
                                ? t('settings.provider.loggingInCountdown', { time: formatCountdown(claudeLoginRemaining) })
                                : t('settings.provider.loggingIn'))
                            : t('settings.provider.loginWithClaude')}
                        </button>
                      )}
                    </div>
                  )}

                  {/* Web-mode fallback: no Tauri IPC, user goes to terminal. */}
                  {!isTauri() && (
                    <p className="text-sm text-[var(--text-tertiary)]">
                      {claudeStatus.cli_installed
                        ? t('settings.provider.webModeInstalled')
                        : t('settings.provider.webModeNotInstalled')}
                    </p>
                  )}
                  {!claudeStatus.cli_installed && isTauri() && (
                    <p className="text-sm text-[var(--text-tertiary)]">
                      {t('settings.provider.cliNotInBundle')}
                    </p>
                  )}
                </div>

                {/* ---- Section B: Provider record state ---- */}
                <div className="pt-2 border-t border-[var(--border-subtle)]">
                  {hasClaude ? (
                    <div className="flex items-center gap-2 text-sm text-[var(--color-success)]">
                      <span>{'\u2713'}</span>
                      <span>{t('settings.provider.addedAsProvider')}</span>
                    </div>
                  ) : claudeStatus.logged_in ? (
                    <button onClick={handleAddClaudeOAuth}
                      className="px-4 py-2 text-sm font-medium rounded-lg bg-[var(--text-primary)] text-[var(--text-inverse)] hover:opacity-90 transition-colors">
                      {t('settings.provider.addAsProvider')}
                    </button>
                  ) : (
                    <p className="text-sm text-[var(--text-tertiary)]">
                      {t('settings.provider.loginToAdd')}
                    </p>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* ---- Codex CLI Login Card ----
            *
            * Parallel to "Claude Code Login" above. Same two-layer
            * model:
            *   1. OS credential state — owned by the `codex` CLI and
            *      stored in `~/.codex/auth.json`. Login is a terminal
            *      action (`codex login` opens a browser); we surface
            *      status only — no Tauri IPC for codex yet, so the
            *      card always shows the "run codex login" hint.
            *   2. Provider record state — owned by NarraNexus and
            *      stored in `user_providers`. Drives "Add as Provider"
            *      / "Added ✓" affordance.
            *
            * Once added as a provider, the Codex OAuth credential
            * becomes assignable to the agent slot. The backend
            * auto-installs ``@openai/codex`` when the user picks
            * "Codex CLI" as the Agent Framework, so by the time the
            * user sees this card the binary is usually already on
            * PATH.
            */}
          <div className="p-4 rounded-xl border border-[var(--accent-primary)]/20 bg-[var(--accent-primary)]/5">
            <div className="flex items-center gap-2 mb-1">
              <h4 className="text-sm font-medium text-[var(--text-primary)]">Codex CLI Login</h4>
            </div>
            <p className="text-sm text-[var(--text-tertiary)] mb-3">
              OAuth login via Codex CLI (Sign in with ChatGPT). No API key needed.
              Usage covered by your ChatGPT Plus / Pro subscription.
            </p>

            {!codexStatus && (
              <p className="text-sm text-[var(--text-tertiary)]">Checking status...</p>
            )}

            {codexStatus && (
              <div className="space-y-3">
                {/* ---- Section A: OS credential state ---- */}
                <div className="space-y-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={cn('inline-block w-2 h-2 rounded-full',
                      codexStatus.logged_in ? 'bg-[var(--color-success)]' :
                      codexStatus.cli_installed ? 'bg-[var(--color-warning)]' : 'bg-[var(--text-tertiary)]'
                    )} />
                    <span className="text-sm text-[var(--text-secondary)]">
                      {codexStatus.logged_in
                        ? <>Logged in{codexStatus.email ? <> as <span className="font-mono">{codexStatus.email}</span></> : null}</>
                        : codexStatus.cli_installed ? 'Not logged in' : 'CLI not installed'}
                    </span>
                    {codexStatus.logged_in && codexStatus.expires_at && (
                      <span className="text-xs text-[var(--text-tertiary)]">
                        {'·'} expires {formatExpiresAt(codexStatus.expires_at)}
                      </span>
                    )}
                  </div>

                  {/* Always show terminal hint. Codex CLI's OAuth flow
                    * opens a browser when `codex login` runs; we don't
                    * shell out via Tauri yet (unlike claude). */}
                  <p className="text-sm text-[var(--text-tertiary)]">
                    {codexStatus.cli_installed
                      ? 'Run "codex login" / "codex logout" in your terminal, then refresh this page.'
                      : 'Install Codex CLI first (auto-installs when you pick "Codex CLI" in the Agent Framework dropdown below), then run "codex login" in your terminal.'}
                  </p>
                </div>

                {/* ---- Section B: Provider record state ---- */}
                <div className="pt-2 border-t border-[var(--border-subtle)]">
                  {hasCodex ? (
                    <div className="flex items-center gap-2 text-sm text-[var(--color-success)]">
                      <span>{'✓'}</span>
                      <span>Added as a NarraNexus provider {'—'} assignable in Step 2 below.</span>
                    </div>
                  ) : codexStatus.logged_in ? (
                    <button onClick={handleAddCodexOAuth}
                      className="px-4 py-2 text-sm font-medium rounded-lg bg-[var(--text-primary)] text-[var(--text-inverse)] hover:opacity-90 transition-colors">
                      Add as Provider
                    </button>
                  ) : (
                    <p className="text-sm text-[var(--text-tertiary)]">
                      Log in above to add Codex CLI as a provider.
                    </p>
                  )}
                </div>
              </div>
            )}
          </div>
          </>)}

          {addMethod === 'custom' && (
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
                className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
              >
                <option value="">{t('settings.provider.selectProtocol')}</option>
                <option value="openai">{t('settings.provider.protocolOpenai')}</option>
                <option value="anthropic">{t('settings.provider.protocolAnthropic')}</option>
              </select>
            </div>

            {/* Step 2: the endpoint fields (shown once a protocol is chosen). */}
            {showForm && (
              <div className="p-4 rounded-xl border border-[var(--border-default)] bg-[var(--bg-tertiary)] space-y-3">
                <p className="text-sm text-[var(--text-tertiary)]">
                  {showForm === 'anthropic' ? t('settings.provider.anthropicEndpointHint') : t('settings.provider.openaiEndpointHint')}
                </p>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-sm text-[var(--text-tertiary)] mb-1">{t('settings.provider.providerNameLabel')}</label>
                    <input type="text" value={formName} onChange={(e) => setFormName(e.target.value)}
                      placeholder={showForm === 'anthropic' ? t('settings.provider.providerNameEgAnthropic') : t('settings.provider.providerNameEgOpenai')}
                      className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
                  </div>
                  {showForm === 'anthropic' ? (
                    <div>
                      <label className="block text-sm text-[var(--text-tertiary)] mb-1">{t('settings.provider.authType')}</label>
                      <select value={formAuth} onChange={(e) => setFormAuth(e.target.value as 'api_key' | 'bearer_token')}
                        className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none">
                        <option value="api_key">{t('settings.provider.authApiKey')}</option>
                        <option value="bearer_token">{t('settings.provider.authBearerToken')}</option>
                      </select>
                    </div>
                  ) : <div />}
                </div>
                <div>
                  <label className="block text-sm text-[var(--text-tertiary)] mb-1">{t('settings.provider.baseUrl')}</label>
                  <input type="text" value={formUrl} onChange={(e) => setFormUrl(e.target.value)}
                    placeholder={t('settings.provider.baseUrl')}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
                </div>
                <div>
                  <label className="block text-sm text-[var(--text-tertiary)] mb-1">{t('settings.provider.apiKeyLabel')}</label>
                  <input type="password" value={formKey} onChange={(e) => setFormKey(e.target.value)}
                    placeholder={t('settings.provider.yourApiKey')}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
                </div>
                <div>
                  <label className="block text-sm text-[var(--text-tertiary)] mb-1">{t('settings.provider.availableModels')}</label>
                  <ModelBubbleInput
                    models={formModels}
                    onChange={setFormModels}
                    suggestions={MODEL_SUGGESTION_GROUPS}
                  />
                </div>
                <button onClick={handleAddProtocol} disabled={formAdding || !formKey.trim()}
                  className="w-full py-2.5 text-sm font-medium rounded-lg bg-[var(--text-primary)] text-[var(--text-inverse)] hover:opacity-90 disabled:opacity-40 transition-colors">
                  {formAdding ? t('settings.provider.adding') : t('settings.provider.addProvider')}
                </button>
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
                    <span className="text-[var(--text-tertiary)]">Endpoint: </span>
                    <span className="font-mono text-xs break-all">{prov.base_url}</span>
                  </div>
                )}
                <div>
                  <span className="text-[var(--text-tertiary)]">API key: </span>
                  <span className="font-mono text-xs">{prov.api_key_masked || '—'}</span>
                </div>
                {prov.netmind_account_email && (
                  <div>
                    <span className="text-[var(--text-tertiary)]">NetMind account: </span>
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
              <button onClick={() => handleTest(prov.provider_id)} disabled={testing === prov.provider_id}
                className="px-4 py-2 text-sm rounded-lg text-[var(--accent-primary)] hover:bg-[var(--accent-primary)]/5 disabled:opacity-40 transition-colors">
                {testing === prov.provider_id ? '...' : t('settings.provider.test')}
              </button>
              <button onClick={() => openEditModels(prov)}
                className="px-4 py-2 text-sm rounded-lg border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] transition-colors">
                {t('settings.provider.edit')}
              </button>
              <button onClick={() => { handleDelete(prov.provider_id); setDetailProviderId(null) }}
                className="px-4 py-2 text-sm rounded-lg text-[var(--color-error)] hover:bg-[var(--color-error)]/5 transition-colors">
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
                className="px-4 py-2 text-sm rounded-lg border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] disabled:opacity-40 transition-colors"
              >
                {t('settings.provider.cancel')}
              </button>
              <button
                onClick={saveEditModels}
                disabled={editSaving}
                className="px-4 py-2 text-sm font-medium rounded-lg bg-[var(--accent-primary)] text-[var(--text-inverse)] hover:bg-[var(--accent-primary)]/90 disabled:opacity-40 transition-colors"
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
