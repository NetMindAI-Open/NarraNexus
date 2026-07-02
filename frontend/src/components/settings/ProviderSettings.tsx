/**
 * @file ProviderSettings.tsx
 * @description LLM Provider configuration for the web frontend Settings modal
 *
 * Layout (always expanded, no collapsed state):
 *
 *   ┌─────────────────────────────────────────┐
 *   │  SECTION 1: Add Providers               │
 *   │  ┌ Quick Add (preset selector + key) ─┐ │
 *   │  │ Claude Code Login card              │ │
 *   │  │ + Anthropic / + OpenAI buttons      │ │
 *   │  │ Configured Providers list           │ │
 *   │  └────────────────────────────────────-┘ │
 *   ├─────────────────────────────────────────┤
 *   │  SECTION 2: Model Assignment            │
 *   │  ┌ Agent slot ────────────────────────┐ │
 *   │  │ Helper LLM slot                   │ │
 *   │  │ Apply / Discard                    │ │
 *   │  └───────────────────────────────────-┘ │
 *   └─────────────────────────────────────────┘
 *
 * Uses the bioluminescent terminal design system CSS variables.
 */

import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { useConfigStore } from '@/stores'
import { getApiBaseUrl } from '@/stores/runtimeStore'
import { Dialog, DialogContent, DialogFooter } from '@/components/ui'
import { api } from '@/lib/api'
import { isTauri, triggerClaudeLogin, triggerClaudeLogout, cancelClaudeLogin } from '@/lib/tauri'

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
}

interface SlotConfig {
  provider_id: string
  model: string
  // Framework-neutral reasoning params (agent slot only). '' = Auto =
  // backend adapter passes nothing and the framework keeps its defaults.
  thinking?: '' | 'on' | 'off'
  reasoning_effort?: '' | 'low' | 'medium' | 'high' | 'max'
}

interface SlotData {
  config: SlotConfig | null
  required_protocols: string[]
}

interface KnownModelMeta {
  model_id: string
  display_name: string
  max_output_tokens: number | null
}

// Preset quick-add moved to the shared OneKeyOnboard component (one-key
// setup via POST /api/providers/onboard) — the provider list, Get Key
// URLs, and recommended default models now live there / in
// model_catalog._ONBOARD_*_MODELS.

// =============================================================================
// Agent Framework definitions
// =============================================================================

interface AgentFramework {
  id: string
  label: string
  protocol: string
  desc: string
}

// One framework name per agent kind. ``codex_cli`` runs the official
// ``openai-codex`` Python SDK under the hood; the older hand-rolled
// implementation file is kept in the repo as a revival fallback only,
// not registered as a driver.
const AGENT_FRAMEWORKS: AgentFramework[] = [
  { id: 'claude_code', label: 'Claude Code', protocol: 'anthropic', desc: 'Claude Agent SDK via Claude Code CLI' },
  { id: 'codex_cli', label: 'Codex CLI', protocol: 'openai', desc: 'Official openai-codex SDK — streaming reasoning + RPC interrupt' },
]

// Codex-framework predicate. Kept as a helper rather than an inline
// ``=== 'codex_cli'`` comparison so a future v3 framework id lands in
// one spot instead of being scattered across three UI branches
// (model curation, provider source filter, install banner).
const isCodexFramework = (framework: string | null | undefined): boolean =>
  framework === 'codex_cli'

const SLOT_DEFS: { key: string; label: string; desc: string; protocol: string }[] = [
  { key: 'agent', label: 'Agent', desc: 'Main dialogue (Anthropic)', protocol: 'anthropic' },
  { key: 'helper_llm', label: 'Helper LLM', desc: 'Auxiliary tasks (OpenAI / Anthropic)', protocol: 'openai' },
]

// What the helper_llm "Default (recommended)" option actually resolves to,
// per provider protocol. Mirrors backend ``_ONBOARD_HELPER_MODELS`` in
// model_catalog.py (openai → gpt-5.4-mini, anthropic → claude-haiku-4-5).
// Surfaced in the option label so users aren't left guessing what "default"
// means. Keep in sync with the backend map.
const RECOMMENDED_HELPER_MODEL_BY_PROTOCOL: Record<string, string> = {
  openai: 'gpt-5.4-mini',
  anthropic: 'claude-haiku-4-5',
}

// =============================================================================
// Model Name Suggestions
// =============================================================================
//
// UX affordance for users who don't know the exact model IDs off the top
// of their head. Rendered as dimmed chips below the ModelBubbleInput —
// click a chip and it moves into the selected-models bubble row.
//
// Why we DON'T filter by the form's protocol: in practice almost all of
// our users hit providers through a forwarding platform (Yunwu /
// OpenRouter / NetMind / custom proxies) that does its own protocol
// translation. A Custom Anthropic endpoint may be fronting GPT, Gemini,
// or MiniMax; a Custom OpenAI endpoint may be fronting Claude. Hiding
// suggestions based on the form's declared protocol would steer users
// away from valid model names. Always show the full set and let the
// user pick — they know what their forwarder exposes.
//
// List curation rules:
//   - OpenAI: 10 newest text / chat / reasoning models
//   - Anthropic: all current models
//   - Gemini: all current text models, exclude deprecated / embedding /
//     audio / live / TTS / image / computer-use
//   - Chinese vendors (Zhipu, Kimi, Qwen, MiniMax, DeepSeek): top 3
//     newest text models each (Kimi has only 1 listed)
//   - DeepSeek compatibility aliases (deepseek-chat, deepseek-reasoner)
//     excluded — users who want them can still type them in manually.

interface ModelSuggestionGroup {
  label: string
  models: string[]
}

const MODEL_SUGGESTION_GROUPS: ModelSuggestionGroup[] = [
  {
    label: 'Anthropic',
    models: [
      'claude-opus-4-8',
      'claude-sonnet-4-6',
      'claude-haiku-4-5',
      'claude-haiku-4-5-20251001',
    ],
  },
  {
    label: 'OpenAI',
    models: [
      'gpt-5.4',
      'gpt-5.4-mini',
      'gpt-5.4-nano',
      'gpt-5.2',
      'gpt-5.2-mini',
      'gpt-5.1',
      'gpt-5',
      'gpt-4.1',
      'o4-mini',
      'o3',
    ],
  },
  {
    label: 'Google Gemini',
    models: [
      'gemini-3.1-pro-preview',
      'gemini-3.1-pro-preview-customtools',
      'gemini-3-flash-preview',
      'gemini-3.1-flash-lite-preview',
      'gemini-2.5-pro',
      'gemini-2.5-flash',
      'gemini-2.5-flash-lite',
      'gemini-deep-research-preview',
      'gemini-deep-research-max-preview',
    ],
  },
  {
    label: 'Zhipu / GLM',
    models: ['glm-5.1', 'glm-5', 'glm-5-turbo'],
  },
  {
    label: 'Kimi (Moonshot)',
    models: ['kimi-k2.6'],
  },
  {
    label: 'Qwen (DashScope)',
    models: ['qwen3.6-max-preview', 'qwen3.6-plus', 'qwen3.6-flash'],
  },
  {
    label: 'MiniMax',
    models: ['MiniMax-M2.7', 'MiniMax-M2.7-highspeed', 'MiniMax-M2.5'],
  },
  {
    label: 'DeepSeek',
    models: ['deepseek-v4-pro', 'deepseek-v4-flash'],
  },
]

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

function SectionHeader({ step, title, subtitle }: { step: number; title: string; subtitle: string }) {
  return (
    <div className="mb-4">
      <div className="flex items-baseline gap-3 mb-1">
        <span className="text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.18em] text-[var(--text-tertiary)] tabular-nums">
          {String(step).padStart(2, '0')}
        </span>
        <h3 className="text-base font-[family-name:var(--font-display)] font-semibold text-[var(--text-primary)] tracking-tight">
          {title}
        </h3>
      </div>
      <p className="text-sm text-[var(--text-tertiary)] ml-[44px] leading-relaxed">{subtitle}</p>
    </div>
  )
}

// =============================================================================
// Main Component
// =============================================================================

// Security hardening (2026-06-17): user-supplied custom (arbitrary
// base_url) providers let an agent's LLM traffic be pointed at an
// external endpoint. The "+ Custom Anthropic / + Custom OpenAI" add
// flow is temporarily disabled pending the workspace/credential
// isolation work. Flip this back to `true` to restore it — the form
// code below is preserved and only gated, so re-enabling is one line.
const CUSTOM_PROVIDER_ENABLED = false

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
  const [slots, setSlots] = useState<Record<string, SlotData>>({})
  const [knownModels, setKnownModels] = useState<Record<string, KnownModelMeta>>({})
  const [officialBaseUrls, setOfficialBaseUrls] = useState<Record<string, string[]>>({})
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

  // Agent framework — loaded from backend on mount + on every refresh.
  // ``probe`` reports whether the chosen framework's host CLI auth is
  // currently usable (e.g. ``codex login`` was completed). null until
  // the first fetch lands.
  const [agentFramework, setAgentFramework] = useState<string>(AGENT_FRAMEWORKS[0].id)
  const [agentFrameworkProbe, setAgentFrameworkProbe] = useState<{ ok: boolean; detail: string } | null>(null)
  const [agentFrameworkSaving, setAgentFrameworkSaving] = useState(false)
  const [agentFrameworkError, setAgentFrameworkError] = useState<string>('')
  // Install banner — surfaced after switching to codex_cli. Post-2026-06-08
  // the codex binary ships as a Python wheel (``openai-codex-cli-bin``)
  // so the install side-effect is just a wheel-presence check; no more
  // npm path. ``auto_installed`` / ``blocked`` no longer fire from the
  // backend but the union keeps them for forward-compat in case we
  // add a different fallback later.
  const [agentFrameworkInstall, setAgentFrameworkInstall] = useState<{
    installed: boolean
    action: 'already_installed' | 'auto_installed' | 'blocked' | 'install_failed'
    reason: string
  } | null>(null)

  // Pending slot changes (local draft, not yet submitted)
  const [pendingSlots, setPendingSlots] = useState<Record<string, SlotConfig>>({})
  const [applying, setApplying] = useState(false)

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

  // ---- Data loading ----
  const refreshConfig = useCallback(async () => {
    try {
      const [cfgRes, catRes, claudeRes, codexRes] = await Promise.all([
        authFetch(providerUrl()).then((r) => r.json()),
        authFetch(providerUrl('/catalog')).then((r) => r.json()),
        authFetch(providerUrl('/claude-status')).then((r) => r.json()).catch(() => null),
        authFetch(providerUrl('/codex-status')).then((r) => r.json()).catch(() => null),
      ])
      if (claudeRes?.success) setClaudeStatus(claudeRes.data)
      if (codexRes?.success) setCodexStatus(codexRes.data)
      if (cfgRes.success) {
        setProviders(cfgRes.data.providers)
        setSlots(cfgRes.data.slots)
        setPendingSlots({})
      }
      if (catRes.success) {
        setKnownModels(catRes.known_models)
        if (catRes.official_base_urls) setOfficialBaseUrls(catRes.official_base_urls)
      }
    } catch (err) {
      console.error('[ProviderSettings] refreshConfig failed:', err)
    }
  }, [providerUrl])

  useEffect(() => { refreshConfig() }, [refreshConfig])

  // Load the user's coding-agent framework choice + auth probe on
  // mount and whenever refreshConfig fires (so a Settings page
  // re-open re-checks whether the OAuth file is still present).
  useEffect(() => {
    let cancelled = false
    api.getAgentFramework().then((resp) => {
      if (cancelled) return
      if (resp.success) {
        setAgentFramework(resp.data.framework)
        setAgentFrameworkProbe(resp.data.probe)
      }
    }).catch((err: unknown) => {
      if (cancelled) return
      // Non-fatal — keep the default selection visible
      console.error('[ProviderSettings] getAgentFramework failed:', err)
    })
    return () => { cancelled = true }
  }, [refreshConfig])

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

  // Compute effective config per slot: pending overrides server state
  const getEffectiveSlotConfig = (slotKey: string): SlotConfig | null => {
    if (pendingSlots[slotKey]) return pendingSlots[slotKey]
    return slots[slotKey]?.config || null
  }

  const allSlotsReady = SLOT_DEFS.every((s) => {
    const cfg = getEffectiveSlotConfig(s.key)
    return cfg?.provider_id && cfg?.model
  })

  const hasPendingChanges = Object.keys(pendingSlots).length > 0

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
    setPendingSlots((prev) => {
      const next = { ...prev }
      for (const [k, v] of Object.entries(next)) {
        if (v.provider_id === id) delete next[k]
      }
      return next
    })
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
  const handleLocalSlotChange = (slot: string, pid: string, model: string) => {
    const cur = getEffectiveSlotConfig(slot)
    setPendingSlots((prev) => ({
      ...prev,
      [slot]: {
        provider_id: pid,
        model,
        thinking: cur?.thinking || '',
        reasoning_effort: cur?.reasoning_effort || '',
      },
    }))
  }

  // Reasoning param change (agent slot). Requires an effective provider —
  // the dropdowns are disabled until one is selected.
  const handleLocalReasoningChange = (
    slot: string,
    field: 'thinking' | 'reasoning_effort',
    value: string,
  ) => {
    const cur = getEffectiveSlotConfig(slot)
    if (!cur?.provider_id) return
    setPendingSlots((prev) => ({
      ...prev,
      [slot]: {
        provider_id: cur.provider_id,
        model: cur.model,
        thinking: cur.thinking || '',
        reasoning_effort: cur.reasoning_effort || '',
        [field]: value,
      },
    }))
  }

  // Apply all pending slot changes to backend
  const handleApply = async () => {
    setApplying(true)
    setError('')
    try {
      for (const [slot, cfg] of Object.entries(pendingSlots)) {
        const res = await authFetch(providerUrl(`/slots/${slot}`), {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            provider_id: cfg.provider_id,
            model: cfg.model,
            thinking: cfg.thinking || '',
            reasoning_effort: cfg.reasoning_effort || '',
          }),
        }).then((r) => r.json())
        if (!res.success) {
          setError(t('settings.provider.failedToSetSlot', { slot, detail: res.detail || t('settings.provider.unknownError') }))
          break
        }
      }
      await refreshConfig()
    } catch {
      setError(t('settings.provider.applyNetworkError'))
    }
    setApplying(false)
  }

  const handleDiscard = () => { setPendingSlots({}) }

  const openForm = (protocol: 'anthropic' | 'openai') => {
    setShowForm(protocol)
    setFormName('')
    setFormUrl(protocol === 'anthropic' ? 'https://api.anthropic.com' : 'https://api.openai.com/v1')
    setFormKey(''); setFormAuth('api_key'); setFormModels([]); setError('')
  }

  const isOfficialProvider = (prov: ProviderSummary) => {
    const urls = officialBaseUrls[prov.protocol] || []
    return urls.includes(prov.base_url || '')
  }

  // Curated model list the Codex CLI subprocess actually accepts.
  // Must stay in sync with backend ``CODEX_CURATED_MODELS`` in
  // ``user_provider_service.py`` — verified 2026-06-02 from
  // codex CLI's interactive "Select Model and Effort" picker.
  // Both codex_oauth and "Custom OpenAI provider used as codex_cli
  // agent" funnel through this list; otherwise the dropdown would
  // offer e.g. gpt-4.1 / o3 which codex CLI rejects at runtime.
  const CODEX_CURATED_MODELS = ['gpt-5.5', 'gpt-5.4', 'gpt-5.4-mini']

  // Provider sources the codex_cli framework actually works with.
  // Codex CLI expects OpenAI's Responses API; third-party
  // aggregators (NetMind / Yunwu / OpenRouter / etc.) only expose
  // ``chat/completions``, so codex CLI silently fails — model is
  // missing, tool-calling format mismatches, MCP integration
  // broken. Without testing each aggregator against codex CLI
  // we can't claim support. Whitelist what's verified:
  //   * codex_oauth — ChatGPT login (OpenAI's own backend)
  //   * user        — Provider added via "+ Custom OpenAI" form
  //                  (assumes user pointed it at api.openai.com or
  //                  a Responses-API-compatible endpoint — that's
  //                  on the user to get right).
  // Anything else (netmind, yunwu, openrouter, etc.) is hidden
  // from the agent slot dropdown when framework=codex_cli.
  // Note: "openai" is a PROTOCOL value, not a SOURCE — preset
  // aggregators are openai-protocol too. Filter by source, not
  // protocol (protocol is already enforced upstream by the slot
  // framework check).
  const CODEX_ALLOWED_PROVIDER_SOURCES = ['codex_oauth', 'user']

  const getModelsForSlot = (prov: ProviderSummary, slotKey: string) => {
    // Agent slot + codex_cli framework → always show the codex-
    // curated set, regardless of provider source AND regardless of
    // whether the provider's stored ``models`` happens to include
    // them. A Custom OpenAI provider's ``models`` column is the
    // user's "I have access to these" claim for non-agent slots
    // (helper_llm). The codex CLI subprocess talks straight to
    // OpenAI with the API key — OpenAI's tier check is the real
    // gate, not this list. Offer all three; if the user's tier
    // doesn't permit one, they'll see a clear error at run time.
    if (slotKey === 'agent' && isCodexFramework(agentFramework)) {
      return CODEX_CURATED_MODELS.map((mid) => ({
        model_id: mid,
        display_name: knownModels[mid]?.display_name || mid,
      }))
    }
    return prov.models
      .map((mid) => ({ model_id: mid, display_name: knownModels[mid]?.display_name || mid }))
  }

  // Switching the agent framework is staff-only in cloud (a framework
  // with no API-key provider falls back to the shared CLI credentials —
  // the backend 403s it; see providers.py §3 gate). The status routes
  // already encode that exact predicate as ``allowed === false``, so we
  // reuse it rather than re-deriving cloud + staff here. Fail-open on the
  // UI if neither status loaded — the backend stays the security boundary.
  const frameworkSwitchBlocked =
    claudeStatus?.allowed === false || codexStatus?.allowed === false

  // ---- Slot row renderer ----
  const renderSlotRow = (slot: typeof SLOT_DEFS[number]) => {
    const selectedFramework = AGENT_FRAMEWORKS.find((f) => f.id === agentFramework)
    // Protocols this slot accepts. Agent follows the selected framework;
    // other slots use the SERVER's required_protocols (helper_llm is
    // [openai, anthropic] since the one-key work — a hardcoded 'openai'
    // here silently hid anthropic providers from the helper dropdown).
    const effectiveProtocols: string[] = slot.key === 'agent' && selectedFramework
      ? [selectedFramework.protocol]
      : (slots[slot.key]?.required_protocols?.length
          ? slots[slot.key].required_protocols
          : [slot.protocol])

    const cfg = getEffectiveSlotConfig(slot.key)
    const ready = !!(cfg?.provider_id && cfg?.model)
    // Agent slot + codex_cli framework → hide third-party
    // aggregators that codex CLI can't talk to (Responses API
    // gate); see CODEX_ALLOWED_PROVIDER_SOURCES above.
    // Helper slot → hide OAuth providers (claude_oauth / codex_oauth):
    // CLI OAuth credentials only drive the agent subprocess and cannot
    // make direct Messages / Chat-Completions calls — picking one here
    // would only fail at agent-loop time with NotImplementedError.
    const matching = providerList.filter((p) =>
      effectiveProtocols.includes(p.protocol) && p.is_active &&
      (
        !(slot.key === 'agent' && isCodexFramework(agentFramework)) ||
        CODEX_ALLOWED_PROVIDER_SOURCES.includes(p.source)
      ) &&
      !(slot.key === 'helper_llm' && p.auth_type === 'oauth')
    )
    const curProv = cfg?.provider_id ? providers[cfg.provider_id] : null
    const isChanged = !!pendingSlots[slot.key]
    const slotDesc = slot.key === 'agent' && selectedFramework
      ? `Main dialogue (${selectedFramework.label})`
      : slot.desc

    return (
      <div key={slot.key} className={cn('p-4 rounded-xl border',
        isChanged ? 'border-[var(--accent-primary)]/30 bg-[var(--accent-primary)]/5' :
        ready ? 'border-[var(--color-success)]/20 bg-[var(--color-success)]/5' : 'border-[var(--color-error)]/20 bg-[var(--color-error)]/5'
      )}>
        <div className="flex items-center justify-between mb-3">
          <span className="text-sm font-medium text-[var(--text-primary)]">
            {slot.label}
            <span className="text-[var(--text-tertiary)] font-normal ml-2">{slotDesc}</span>
          </span>
          <div className="flex items-center gap-2">
            {isChanged && <span className="text-xs text-[var(--accent-primary)]">{t('settings.provider.modified')}</span>}
            {ready
              ? <span className="text-[var(--color-success)] text-base">{'\u2713'}</span>
              : <span className="text-sm text-[var(--color-error)]">{t('settings.provider.needed')}</span>}
          </div>
        </div>

        {/* Agent Framework selector */}
        {slot.key === 'agent' && (
          <div className="mb-3">
            <label className="block text-sm text-[var(--text-tertiary)] mb-1">
              {t('settings.provider.agentFramework')}
              {agentFrameworkProbe !== null && (
                <span
                  className={`ml-2 text-xs ${
                    agentFrameworkProbe.ok
                      ? 'text-[var(--color-success)]'
                      : 'text-[var(--color-error)]'
                  }`}
                  title={agentFrameworkProbe.detail}
                >
                  {agentFrameworkProbe.ok ? '✓ auth ready' : '✗ auth missing'}
                </span>
              )}
            </label>
            {frameworkSwitchBlocked ? (
              // Cloud + non-staff: switching is backend-gated (403). Show the
              // current choice read-only instead of a control that errors.
              <div className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-secondary)] text-[var(--text-secondary)]">
                {selectedFramework
                  ? `${selectedFramework.label} — ${selectedFramework.desc}`
                  : agentFramework}
                <span className="ml-2 text-xs text-[var(--text-tertiary)]">
                  · managed by staff in cloud
                </span>
              </div>
            ) : (
              <select
                value={agentFramework}
                disabled={agentFrameworkSaving}
                onChange={async (e) => {
                  const next = e.target.value
                  setAgentFramework(next)
                  setAgentFrameworkSaving(true)
                  setAgentFrameworkError('')
                  setAgentFrameworkInstall(null)
                  try {
                    const resp = await api.setAgentFramework(next)
                    if (resp.success) {
                      setAgentFrameworkProbe(resp.data.probe)
                      setAgentFrameworkInstall(resp.data.install)
                    }
                  } catch (err: unknown) {
                    setAgentFrameworkError(
                      err instanceof Error ? err.message : 'Failed to save framework'
                    )
                  } finally {
                    setAgentFrameworkSaving(false)
                  }
                }}
                className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)] disabled:opacity-50"
              >
                {AGENT_FRAMEWORKS.map((fw) => (
                  <option key={fw.id} value={fw.id}>{fw.label} — {fw.desc}</option>
                ))}
              </select>
            )}
            {agentFrameworkSaving && isCodexFramework(agentFramework) && (
              <div className="text-xs text-[var(--text-tertiary)] mt-1 italic">
                {'Verifying Codex CLI…'}
              </div>
            )}
            {agentFrameworkError && (
              <div className="text-xs text-[var(--color-error)] mt-1">
                {agentFrameworkError}
              </div>
            )}
            {agentFrameworkInstall && agentFrameworkInstall.action === 'install_failed' && (
              <div className="text-xs text-[var(--color-error)] mt-1">
                Codex binary unavailable: {agentFrameworkInstall.reason}
              </div>
            )}
            {agentFrameworkProbe !== null && !agentFrameworkProbe.ok &&
             !(agentFrameworkInstall && agentFrameworkInstall.action === 'install_failed') && (
              <div className="text-xs text-[var(--text-tertiary)] mt-1">
                {agentFrameworkProbe.detail}
              </div>
            )}
          </div>
        )}

        {matching.length > 0 ? (
          <div className="grid grid-cols-2 gap-3">
            {/* Provider dropdown */}
            <div>
              <label className="block text-sm text-[var(--text-tertiary)] mb-1">{t('settings.provider.providerLabel')}</label>
              <select value={cfg?.provider_id || ''}
                onChange={(e) => {
                  const pid = e.target.value
                  const prov = providers[pid]
                  if (!prov) return
                  const slotModels = getModelsForSlot(prov, slot.key)
                  if (slot.key === 'helper_llm' && isOfficialProvider(prov)) {
                    handleLocalSlotChange(slot.key, pid, 'default')
                  } else if (slotModels.length > 0) {
                    handleLocalSlotChange(slot.key, pid, slotModels[0].model_id)
                  } else {
                    handleLocalSlotChange(slot.key, pid, '')
                  }
                }}
                className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]">
                <option value="">{t('settings.provider.selectProvider')}</option>
                {matching.map((p) => <option key={p.provider_id} value={p.provider_id}>{p.name}</option>)}
              </select>
            </div>

            {/* Model dropdown */}
            <div>
              <label className="block text-sm text-[var(--text-tertiary)] mb-1">{t('settings.provider.modelLabel')}</label>
              {(() => {
                if (!curProv) return (
                  <select disabled className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-tertiary)] outline-none">
                    <option>{t('settings.provider.selectProviderFirst')}</option>
                  </select>
                )

                if (slot.key === 'helper_llm' && isOfficialProvider(curProv)) {
                  const llmModels = getModelsForSlot(curProv, 'helper_llm')
                  // Show which concrete model "Default" resolves to, so the
                  // user isn't left guessing (e.g. "Default · gpt-5.4-mini").
                  const recHelperModel =
                    RECOMMENDED_HELPER_MODEL_BY_PROTOCOL[curProv.protocol] || 'gpt-5.4-mini'
                  const recHelperLabel = knownModels[recHelperModel]?.display_name || recHelperModel
                  return (
                    <>
                      <select value={cfg?.model || ''} onChange={(e) => { if (cfg?.provider_id) handleLocalSlotChange(slot.key, cfg.provider_id, e.target.value) }}
                        className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]">
                        <option value="default">Default · {recHelperLabel} (recommended)</option>
                        {llmModels.map((m) => <option key={m.model_id} value={m.model_id}>{m.display_name}</option>)}
                      </select>
                      {cfg?.model && cfg.model !== 'default' && (
                        <p className="text-xs text-[var(--color-warning)] mt-1">{t('settings.provider.auxModelWarning')}</p>
                      )}
                    </>
                  )
                }

                const llmModels = getModelsForSlot(curProv, slot.key)
                if (llmModels.length > 0) {
                  return (
                    <select value={cfg?.model || ''} onChange={(e) => { if (cfg?.provider_id) handleLocalSlotChange(slot.key, cfg.provider_id, e.target.value) }}
                      className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]">
                      <option value="">{t('settings.provider.selectModel')}</option>
                      {llmModels.map((m) => <option key={m.model_id} value={m.model_id}>{m.display_name}</option>)}
                    </select>
                  )
                }

                return (
                  <input type="text" value={cfg?.model || ''} onChange={(e) => { if (cfg?.provider_id) handleLocalSlotChange(slot.key, cfg.provider_id, e.target.value) }}
                    placeholder={t('settings.provider.enterModelName')}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
                )
              })()}
            </div>

            {/* Reasoning params — agent slot only. Framework-neutral values;
                each backend adapter maps them to its own dialect. Auto = ''
                = adapter passes nothing (framework default behavior). */}
            {slot.key === 'agent' && (
              <>
                <div>
                  <label className="block text-sm text-[var(--text-tertiary)] mb-1">{t('settings.provider.thinking')}</label>
                  <select
                    value={cfg?.thinking || ''}
                    disabled={!cfg?.provider_id}
                    onChange={(e) => handleLocalReasoningChange(slot.key, 'thinking', e.target.value)}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)] disabled:bg-[var(--bg-tertiary)]"
                  >
                    <option value="">{t('settings.provider.autoDefault')}</option>
                    <option value="on">{t('settings.provider.on')}</option>
                    <option value="off">{t('settings.provider.off')}</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm text-[var(--text-tertiary)] mb-1">{t('settings.provider.reasoningEffort')}</label>
                  <select
                    value={cfg?.reasoning_effort || ''}
                    disabled={!cfg?.provider_id}
                    onChange={(e) => handleLocalReasoningChange(slot.key, 'reasoning_effort', e.target.value)}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)] disabled:bg-[var(--bg-tertiary)]"
                  >
                    <option value="">{t('settings.provider.autoDefault')}</option>
                    <option value="low">{t('settings.provider.low')}</option>
                    <option value="medium">{t('settings.provider.medium')}</option>
                    <option value="high">{t('settings.provider.high')}</option>
                    <option value="max">{t('settings.provider.max')}</option>
                  </select>
                </div>
              </>
            )}
          </div>
        ) : (
          <p className="text-sm text-[var(--color-error)]">
            {slot.key === 'agent' && isCodexFramework(agentFramework)
              ? 'Codex CLI needs an OpenAI provider that speaks the Responses API: ' +
                'sign in with Codex CLI (codex login) or add a Custom OpenAI key in Step 1. ' +
                'Aggregator providers (NetMind / Yunwu / OpenRouter) are not supported by Codex.'
              : `No ${effectiveProtocols.join(' / ')} protocol provider configured. Add one in Step 1 above.`}
          </p>
        )}
      </div>
    )
  }

  // ---- Full view (always expanded) ----
  return (
    <div className="space-y-8">

      {/* System free-tier quota is surfaced at the Providers-panel top level
          (SettingsPage), not here — keeping it inside this collapsed-by-
          default Advanced section hid it from cloud users. */}

      {/* ================================================================= */}
      {/* SECTION 1: Add Providers                                          */}
      {/* ================================================================= */}
      <div>
        <SectionHeader
          step={1}
          title={t('settings.provider.section1Title')}
          subtitle={t('settings.provider.section1Subtitle')}
        />

        <div className="space-y-4 ml-[34px]">
          {/* One-key preset setup (pick provider + paste key) now lives at
              the panel level — SettingsPage embeds <OneKeyOnboard> directly,
              and SetupPage shows it as the first-run hero. It used to be
              duplicated here; removed so Advanced doesn't repeat it. This
              section is the rest: model sync, CLI OAuth sign-in, and custom
              (base_url) endpoints. */}

          {/* ---- Sync available models ---- */}
          {/*
            One-click backfill: takes the current default model list out of
            `model_catalog._DEFAULT_MODELS` for every preset source and
            appends any missing entries onto the user's already-configured
            providers. Useful when we ship new models — the user keeps
            their existing slot assignments and provider IDs while picking
            up the additions. Quick Add would re-create + lose those bonds.
          */}
          <div className="p-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-tertiary)]">
            <h4 className="text-sm font-medium text-[var(--text-primary)] mb-1.5">{t('settings.provider.updateModelsTitle')}</h4>
            <p className="text-sm text-[var(--text-tertiary)]">
              {t('settings.provider.updateModelsDesc')}
            </p>
            {/* Gap lives on this row, not the <p>/<h4> above: index.css resets
                `p`/`h*` margins (unlayered), which kills any mb-* utility on
                them — so the spacing must sit on a div. */}
            <div className="flex items-center gap-3 mt-5">
              <button
                onClick={handleSyncDefaults}
                disabled={syncing || !userId}
                className="px-4 py-2 text-sm font-medium rounded-lg bg-[var(--text-primary)] text-[var(--text-inverse)] hover:opacity-90 disabled:opacity-40 transition-colors"
              >
                {syncing ? t('settings.provider.syncing') : t('settings.provider.updateModelsBtn')}
              </button>
              {syncResult && (
                <span
                  className={cn(
                    'text-xs whitespace-pre-wrap leading-relaxed',
                    syncResult.kind === 'ok'
                      ? 'text-[var(--text-secondary)]'
                      : 'text-[var(--color-red-500)]'
                  )}
                >
                  {syncResult.text}
                </span>
              )}
            </div>
          </div>

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

          {/* ---- Custom: Add Protocol Buttons ----
            *
            * Note: API-key Codex flows through ``+ Custom OpenAI`` —
            * the resolver builds CodexConfig from any OpenAI-protocol
            * provider when ``agent_framework=codex_cli`` is set on
            * the slot. No dedicated card needed; auth.json fetch is
            * the only thing the OAuth card adds functionally.
            */}
          {CUSTOM_PROVIDER_ENABLED ? (
          <>
          <div className="flex gap-2">
            <button onClick={() => openForm('anthropic')}
              className="flex-1 py-2.5 text-sm rounded-lg border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] transition-colors">
              {t('settings.provider.customAnthropic')}
            </button>
            <button onClick={() => openForm('openai')}
              className="flex-1 py-2.5 text-sm rounded-lg border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] transition-colors">
              {t('settings.provider.customOpenai')}
            </button>
          </div>

          {/* ---- Protocol Form ---- */}
          {showForm && (
            <div className="p-4 rounded-xl border border-[var(--border-default)] bg-[var(--bg-tertiary)] space-y-3">
              <div className="flex items-center justify-between">
                <h4 className="text-sm font-medium text-[var(--text-primary)]">
                  {showForm === 'anthropic' ? t('settings.provider.customAnthropicProvider') : t('settings.provider.customOpenaiProvider')}
                </h4>
                <button onClick={() => setShowForm(null)} className="text-sm text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]">{t('settings.provider.cancel')}</button>
              </div>
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
          </>
          ) : (
            <div className="p-4 rounded-xl border border-[var(--border-default)] bg-[var(--bg-tertiary)]">
              <h4 className="text-sm font-medium text-[var(--text-primary)] mb-1">
                Adding custom providers is temporarily unavailable
              </h4>
              <p className="text-sm text-[var(--text-tertiary)]">
                Custom (custom base URL) provider setup is paused for security
                hardening and will be restored soon. Your already-configured
                providers remain fully usable.
              </p>
            </div>
          )}

          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}

          {/* ---- Configured Providers List ---- */}
          {hasProviders && (
            <div className="space-y-2">
              <span className="text-xs text-[var(--text-tertiary)] uppercase tracking-wider font-medium">
                {t('settings.provider.configuredProviders')}
              </span>
              {providerList.map((prov) => (
                <div key={prov.provider_id} className="flex items-center justify-between p-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-primary)]">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-[var(--text-primary)] truncate">{prov.name}</span>
                      <span className="text-xs px-2 py-0.5 rounded bg-[var(--bg-tertiary)] text-[var(--text-tertiary)] uppercase">{prov.protocol}</span>
                    </div>
                    <span className="text-sm text-[var(--text-tertiary)]">{prov.api_key_masked} · {t('settings.provider.modelsCount', { count: prov.models.length })}</span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button onClick={() => handleTest(prov.provider_id)} disabled={testing === prov.provider_id}
                      className="px-3 py-1.5 text-sm text-[var(--accent-primary)] hover:bg-[var(--accent-primary)]/5 rounded-lg disabled:opacity-40 transition-colors">
                      {testing === prov.provider_id ? '...' : t('settings.provider.test')}
                    </button>
                    <button onClick={() => openEditModels(prov)}
                      className="px-3 py-1.5 text-sm text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] rounded-lg transition-colors">
                      {t('settings.provider.edit')}
                    </button>
                    <button onClick={() => handleDelete(prov.provider_id)}
                      className="px-3 py-1.5 text-sm text-[var(--color-error)] hover:bg-[var(--color-error)]/5 rounded-lg transition-colors">
                      {t('settings.provider.delete')}
                    </button>
                    {testResults[prov.provider_id] && (
                      <span className={cn('text-sm', testResults[prov.provider_id].ok ? 'text-[var(--color-success)]' : 'text-[var(--color-error)]')}>
                        {testResults[prov.provider_id].msg}
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ================================================================= */}
      {/* SECTION 2: Model Assignment                                        */}
      {/* ================================================================= */}
      {hasProviders && (
        <div>
          <SectionHeader
            step={2}
            title={t('settings.provider.section2Title')}
            subtitle={t('settings.provider.section2Subtitle')}
          />

          <div className="space-y-3 ml-[34px]">
            {SLOT_DEFS.map((slot) => renderSlotRow(slot))}

            {/* Apply / Discard buttons */}
            {hasPendingChanges && (
              <div className="flex gap-3 pt-2">
                <button
                  onClick={handleApply}
                  disabled={applying}
                  className={cn(
                    'flex-1 py-2.5 text-sm font-medium transition-colors',
                    'bg-[var(--text-primary)] text-[var(--text-inverse)]',
                    'hover:opacity-90',
                    'disabled:opacity-40'
                  )}
                >
                  {applying ? t('settings.provider.applying') : t('settings.provider.applyChanges')}
                </button>
                <button
                  onClick={handleDiscard}
                  disabled={applying}
                  className="px-6 py-2.5 text-sm rounded-lg border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] disabled:opacity-40 transition-colors"
                >
                  {t('settings.provider.discard')}
                </button>
              </div>
            )}

            {/* Status indicator */}
            {allSlotsReady && !hasPendingChanges && (
              <div className="flex items-center gap-2 p-3 rounded-xl bg-[var(--color-success)]/10 border border-[var(--color-success)]/20">
                <span className="text-[var(--color-success)] text-base">{'\u2713'}</span>
                <span className="text-sm text-[var(--color-success)]">{t('settings.provider.allSlotsReady')}</span>
              </div>
            )}
          </div>
        </div>
      )}

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
