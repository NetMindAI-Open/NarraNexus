/**
 * agentFramework — the provider↔framework compatibility predicates.
 *
 * `providerBacksFramework` is the frontend twin of backend
 * `provider_schema.framework_can_drive_provider`; `availableFrameworks` is what
 * keeps a picker from offering a dead end. The bug both exist for: a CLI
 * subscription card (Claude Code Login) bound to a nexus_power agent slot saved
 * fine and only failed in the middle of a run.
 *
 * B6 final cut (2026-09-07): `providerBacksFramework` no longer consults any
 * hardcoded table — every assertion below passes an explicit live
 * `frameworks` list (or, for the fail-closed cases, deliberately omits one).
 */
import { describe, test, expect } from 'vitest'
import {
  availableFrameworks,
  providerBacksFramework,
  AGENT_FRAMEWORKS,
  frameworkAvailabilityMap,
  withFrameworkAvailability,
  isFrameworkAvailable,
  defaultAgentModel,
  defaultHelperModel,
  recommendedHelperModel,
  type OnboardDefaults,
} from '../agentFramework'

const card = (source: string, protocol: string, auth_type: string) => ({
  source,
  protocol,
  auth_type,
})

const CLAUDE_LOGIN = card('claude_oauth', 'anthropic', 'oauth')
const CLAUDE_SETUP_TOKEN = card('claude_oauth', 'anthropic', 'oauth_token')
const CODEX_LOGIN = card('codex_oauth', 'openai', 'oauth')
const ANTHROPIC_KEY = card('user', 'anthropic', 'api_key')
const NETMIND_OPENAI = card('netmind', 'openai', 'bearer_token')

// Mirrors what `GET/POST /api/providers/agent-framework`'s `frameworks[]` carries
// once a caller has fully loaded it. `protocol` is the backend's three-valued
// enum (`FrameworkMeta.protocol`): 'anthropic' | 'openai' | 'any' — nexus_power
// is sent as 'any' because it drives the provider API itself and works with
// either.
const LIVE_FRAMEWORKS = [
  { name: 'claude_code', protocol: 'anthropic' as const, oauth_source: 'claude_oauth' },
  { name: 'codex_cli', protocol: 'openai' as const, oauth_source: 'codex_oauth' },
  { name: 'nexus_power', protocol: 'any' as const, oauth_source: null },
]

describe('providerBacksFramework', () => {
  test('no live list yet → fails closed, never guesses from a hardcoded table', () => {
    expect(providerBacksFramework(CLAUDE_LOGIN, 'claude_code', undefined)).toBe(false)
    expect(providerBacksFramework(ANTHROPIC_KEY, 'nexus_power', undefined)).toBe(false)
  })

  test('a subscription card is redeemable only by its own CLI framework', () => {
    expect(providerBacksFramework(CLAUDE_LOGIN, 'claude_code', LIVE_FRAMEWORKS)).toBe(true)
    expect(providerBacksFramework(CLAUDE_SETUP_TOKEN, 'claude_code', LIVE_FRAMEWORKS)).toBe(true)
    expect(providerBacksFramework(CLAUDE_LOGIN, 'nexus_power', LIVE_FRAMEWORKS)).toBe(false)
    expect(providerBacksFramework(CLAUDE_SETUP_TOKEN, 'nexus_power', LIVE_FRAMEWORKS)).toBe(false)
    expect(providerBacksFramework(CODEX_LOGIN, 'codex_cli', LIVE_FRAMEWORKS)).toBe(true)
    expect(providerBacksFramework(CODEX_LOGIN, 'nexus_power', LIVE_FRAMEWORKS)).toBe(false)
  })

  test('no framework chosen yet is NOT read as Claude Code — a subscription card is unredeemable by "nothing"', () => {
    // The fallback used to be a hardcoded `|| 'claude_code'`: an unresolved
    // framework silently became Claude Code, which on the lightweight build
    // is not even installed. The default is the backend's call, never a
    // frontend literal.
    expect(providerBacksFramework(CLAUDE_LOGIN, null, LIVE_FRAMEWORKS)).toBe(false)
    expect(providerBacksFramework(CLAUDE_LOGIN, '', LIVE_FRAMEWORKS)).toBe(false)
    // API-key cards have no redeemer to match and pass as before.
    expect(providerBacksFramework(ANTHROPIC_KEY, null, LIVE_FRAMEWORKS)).toBe(true)
  })

  test('API-key and bearer cards face the live list\'s protocol gate', () => {
    expect(providerBacksFramework(ANTHROPIC_KEY, 'claude_code', LIVE_FRAMEWORKS)).toBe(true)
    expect(providerBacksFramework(ANTHROPIC_KEY, 'codex_cli', LIVE_FRAMEWORKS)).toBe(false)
    expect(providerBacksFramework(NETMIND_OPENAI, 'codex_cli', LIVE_FRAMEWORKS)).toBe(true)
    expect(providerBacksFramework(NETMIND_OPENAI, 'claude_code', LIVE_FRAMEWORKS)).toBe(false)
  })

  test('protocol "any" accepts both anthropic and openai cards — read off the live value, not the framework id', () => {
    expect(providerBacksFramework(ANTHROPIC_KEY, 'nexus_power', LIVE_FRAMEWORKS)).toBe(true)
    expect(providerBacksFramework(NETMIND_OPENAI, 'nexus_power', LIVE_FRAMEWORKS)).toBe(true)
  })

  test('the "any" behavior is driven by the protocol VALUE, not a nexus_power id special-case', () => {
    // A hypothetical framework with a DIFFERENT id but protocol: 'any' behaves
    // identically to nexus_power — proves there is no id-based branch left.
    const anyById = [{ name: 'some_future_framework', protocol: 'any' as const, oauth_source: null }]
    expect(providerBacksFramework(ANTHROPIC_KEY, 'some_future_framework', anyById)).toBe(true)
    expect(providerBacksFramework(NETMIND_OPENAI, 'some_future_framework', anyById)).toBe(true)

    // Conversely, if nexus_power's live entry ever reported a SPECIFIC protocol
    // (not 'any'), it must be gated exactly like any other framework — proves
    // there is no leftover nexus_power-by-id bypass either.
    const nexusPinnedToAnthropic = [{ name: 'nexus_power', protocol: 'anthropic' as const, oauth_source: null }]
    expect(providerBacksFramework(NETMIND_OPENAI, 'nexus_power', nexusPinnedToAnthropic)).toBe(false)
  })

  describe('reads oauth_source straight off the live list — no hardcoded mirror consulted', () => {
    test('a hypothetical remap disagrees with what the old hardcoded mirror said, and the live list wins', () => {
      const remapped = [{ name: 'nexus_power', oauth_source: 'claude_oauth' }]
      expect(providerBacksFramework(CLAUDE_LOGIN, 'nexus_power', remapped)).toBe(true)
      expect(providerBacksFramework(CLAUDE_LOGIN, 'claude_code', remapped)).toBe(false)
    })

    test('an entry missing from the live list is simply not redeemable — there is no fallback left to consult', () => {
      const partial = [{ name: 'codex_cli', oauth_source: 'codex_oauth' }]
      expect(providerBacksFramework(CLAUDE_LOGIN, 'claude_code', partial)).toBe(false)
    })
  })
})

describe('availableFrameworks', () => {
  test('a wallet holding only a Claude Code Login offers only Claude Code', () => {
    const fws = availableFrameworks([CLAUDE_LOGIN], 'claude_code', LIVE_FRAMEWORKS)
    expect(fws.map((f) => f.id)).toEqual(['claude_code'])
  })

  test('adding an API-key card brings NexusPower back', () => {
    const fws = availableFrameworks(
      [CLAUDE_LOGIN, ANTHROPIC_KEY], 'claude_code', LIVE_FRAMEWORKS,
    )
    expect(fws.map((f) => f.id)).toEqual(['claude_code', 'nexus_power'])
  })

  test('an openai-only wallet drops claude_code but keeps NexusPower', () => {
    const fws = availableFrameworks([NETMIND_OPENAI], 'nexus_power', LIVE_FRAMEWORKS)
    expect(fws.map((f) => f.id)).toEqual(['codex_cli', 'nexus_power'])
  })

  test('no providers yet → no filtering (never an empty dropdown), even with no live list', () => {
    expect(availableFrameworks([], 'claude_code', undefined)).toHaveLength(
      AGENT_FRAMEWORKS.length,
    )
  })

  test('the current framework is kept even when nothing can drive it', () => {
    // A stored nexus_power pin with only a Codex login in the wallet: hiding
    // the selected value would silently re-point the <select> elsewhere.
    const fws = availableFrameworks([CODEX_LOGIN], 'nexus_power', LIVE_FRAMEWORKS)
    expect(fws.map((f) => f.id)).toEqual(['codex_cli', 'nexus_power'])
  })

  test('no live list yet → only the current framework survives (fail closed, not a hardcoded guess)', () => {
    const fws = availableFrameworks([CLAUDE_LOGIN, ANTHROPIC_KEY], 'claude_code', undefined)
    expect(fws.map((f) => f.id)).toEqual(['claude_code'])
  })
})

describe('plugin-install availability (frameworkAvailabilityMap / withFrameworkAvailability)', () => {
  test('an uninstalled plugin is marked unavailable, never hidden', () => {
    const map = frameworkAvailabilityMap([
      { name: 'claude_code', available: true },
      { name: 'codex_cli', available: false },
    ])
    const merged = withFrameworkAvailability(AGENT_FRAMEWORKS, map)
    // Still every framework — plugin gating disables, it does not filter.
    expect(merged.map((f) => f.id)).toEqual(AGENT_FRAMEWORKS.map((f) => f.id))
    expect(isFrameworkAvailable(merged.find((f) => f.id === 'claude_code')!)).toBe(true)
    expect(isFrameworkAvailable(merged.find((f) => f.id === 'codex_cli')!)).toBe(false)
  })

  test('a framework the backend never mentioned defaults to available', () => {
    // nexus_power isn't a plugin (no install step) — an older or partial
    // backend response with no entry for it must not lock it out.
    const map = frameworkAvailabilityMap([{ name: 'codex_cli', available: false }])
    const merged = withFrameworkAvailability(AGENT_FRAMEWORKS, map)
    expect(isFrameworkAvailable(merged.find((f) => f.id === 'nexus_power')!)).toBe(true)
  })

  test('an entirely absent frameworks array (older backend) leaves everything available', () => {
    const map = frameworkAvailabilityMap(undefined)
    const merged = withFrameworkAvailability(AGENT_FRAMEWORKS, map)
    expect(merged.every((f) => isFrameworkAvailable(f))).toBe(true)
  })
})

// Picking a provider must land on the platform's onboarding default for that
// source, not on models[0]: NetMind's synced list starts with an omni model.
// The defaults are SERVER-owned (GET /api/providers/catalog -> `onboard_defaults`)
// and handed to the pickers as an explicit argument -- no module-level state.
describe('default model on provider pick (explicit defaults argument)', () => {
  const FLASH = 'deepseek-ai/DeepSeek-V4.1-Flash'
  const listed = ['Qwen/Qwen3.5-Omni-Flash', 'Qwen/Qwen3-32B', FLASH]
  const defaults: OnboardDefaults = {
    netmind: { agent: FLASH, helper: FLASH },
    netmind_free: { agent: FLASH, helper: FLASH },
  }

  test.each(['netmind', 'netmind_free'])('%s agent -> server default, not list head', (source) => {
    expect(defaultAgentModel(source, listed, defaults)).toBe(FLASH)
  })

  test.each(['netmind', 'netmind_free'])('%s helper -> server default, not list head', (source) => {
    expect(defaultHelperModel(source, 'openai', listed, defaults)).toBe(FLASH)
  })

  test('the pick follows whatever defaults it is handed (ops env override for the free card)', () => {
    const ops: OnboardDefaults = { netmind_free: { agent: 'Qwen/Qwen3-32B', helper: FLASH } }
    expect(defaultAgentModel('netmind_free', listed, ops)).toBe('Qwen/Qwen3-32B')
    // a different defaults object has no memory of the previous one
    expect(defaultAgentModel('netmind', listed, ops)).toBe('Qwen/Qwen3.5-Omni-Flash')
  })

  test('no defaults yet (not loaded / failed) -> list head, never empty', () => {
    expect(defaultAgentModel('netmind', listed, {})).toBe('Qwen/Qwen3.5-Omni-Flash')
    expect(defaultHelperModel('netmind', 'openai', ['x', 'y'], {})).toBe('x')
  })

  test('falls back to the list head when the default is not on the card', () => {
    expect(defaultAgentModel('netmind', ['a', 'b'], defaults)).toBe('a')
  })

  test('sources without an onboarding default keep the list head', () => {
    expect(defaultAgentModel('user', ['a', 'b'], defaults)).toBe('a')
    expect(defaultAgentModel('netmind', [], defaults)).toBe('')
  })
})

describe('recommendedHelperModel (the "Recommended: ..." hint)', () => {
  const FLASH = 'deepseek-ai/DeepSeek-V4.1-Flash'
  const defaults: OnboardDefaults = { netmind: { agent: FLASH, helper: FLASH } }

  test('names the deliberate pick when it is on the card (same as the auto-pick)', () => {
    expect(recommendedHelperModel('netmind', 'openai', ['Qwen/Qwen3.5-Omni-Flash', FLASH], defaults)).toBe(FLASH)
    expect(recommendedHelperModel('claude_oauth', 'anthropic', ['opus', 'haiku'], defaults)).toBe('haiku')
    expect(recommendedHelperModel('codex_oauth', 'openai', ['gpt-5.5', 'gpt-5.4-mini'], defaults)).toBe('gpt-5.4-mini')
    expect(recommendedHelperModel('user', 'openai', ['gpt-5.5', 'gpt-5.4-mini'], defaults)).toBe('gpt-5.4-mini')
  })

  test('never names a list head the platform did not pick (curated flagship card)', () => {
    const hint = recommendedHelperModel('user', 'anthropic', ['claude-opus-4-8', 'claude-sonnet-4-6'], defaults)
    expect(hint).not.toBe('claude-opus-4-8')
    expect(hint).toBe('claude-haiku-4-5') // protocol-table model
  })

  test('NetMind card without the default does not recommend the omni head', () => {
    const hint = recommendedHelperModel('netmind', 'openai', ['Qwen/Qwen3.5-Omni-Flash', 'Qwen/Qwen3-32B'], defaults)
    expect(hint).not.toBe('Qwen/Qwen3.5-Omni-Flash')
    expect(hint).toBe('gpt-5.4-mini')
  })

  test('the auto-pick still falls back to the list head (only the hint is stricter)', () => {
    expect(defaultHelperModel('user', 'anthropic', ['claude-opus-4-8', 'claude-sonnet-4-6'], defaults)).toBe('claude-opus-4-8')
  })

  test('no provider chosen yet -> protocol-table fallback, never empty', () => {
    expect(recommendedHelperModel(undefined, undefined, [], defaults)).toBe('gpt-5.4-mini')
    expect(recommendedHelperModel('user', 'anthropic', [], defaults)).toBe('claude-haiku-4-5')
  })
})
