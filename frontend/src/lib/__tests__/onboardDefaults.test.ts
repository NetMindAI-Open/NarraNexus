/**
 * refreshOnboardDefaults loads the server-owned per-source onboarding default
 * models (GET /api/providers/catalog -> onboard_defaults) and RETURNS them; it
 * never rejects and never hides a failure: ok=false + a console.warn, so the
 * panels can say the load failed instead of silently picking the list head
 * (an omni model on NetMind cards).
 */
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

const { mockGetProviderCatalog } = vi.hoisted(() => ({ mockGetProviderCatalog: vi.fn() }))
vi.mock('@/lib/api', () => ({ api: { getProviderCatalog: mockGetProviderCatalog } }))

import { refreshOnboardDefaults } from '../onboardDefaults'

const FLASH = 'deepseek-ai/DeepSeek-V4.1-Flash'
let warn: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  mockGetProviderCatalog.mockReset()
  warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
})
afterEach(() => warn.mockRestore())

test('success returns the server defaults', async () => {
  mockGetProviderCatalog.mockResolvedValue({
    success: true,
    onboard_defaults: { netmind: { agent: FLASH, helper: FLASH } },
  })

  const res = await refreshOnboardDefaults()

  expect(res).toEqual({ ok: true, defaults: { netmind: { agent: FLASH, helper: FLASH } } })
  expect(warn).not.toHaveBeenCalled()
})

test('a rejected request warns, returns ok=false and never rejects', async () => {
  mockGetProviderCatalog.mockRejectedValue(new Error('network'))

  await expect(refreshOnboardDefaults()).resolves.toEqual({ ok: false, defaults: {} })

  expect(warn).toHaveBeenCalledWith('onboard defaults unavailable', expect.any(Error))
})

test('success=false is a failure too', async () => {
  mockGetProviderCatalog.mockResolvedValue({ success: false })

  await expect(refreshOnboardDefaults()).resolves.toEqual({ ok: false, defaults: {} })

  expect(warn).toHaveBeenCalled()
})

test.each([
  ['missing', undefined],
  ['null', null],
  ['an array', []],
  ['a string', 'x'],
])('a %s onboard_defaults field is a failure, not an empty table', async (_name, bad) => {
  mockGetProviderCatalog.mockResolvedValue({ success: true, onboard_defaults: bad })

  await expect(refreshOnboardDefaults()).resolves.toEqual({ ok: false, defaults: {} })

  expect(warn).toHaveBeenCalled()
})

test('malformed entries are dropped, well-formed ones kept', async () => {
  mockGetProviderCatalog.mockResolvedValue({
    success: true,
    onboard_defaults: { netmind: { agent: FLASH, helper: 5 }, broken: 'x', empty: {} },
  })

  const res = await refreshOnboardDefaults()

  expect(res).toEqual({ ok: true, defaults: { netmind: { agent: FLASH } } })
})
