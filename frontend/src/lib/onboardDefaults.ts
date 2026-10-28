/**
 * @file_name: onboardDefaults.ts
 * @description: Load the server-owned per-source onboarding default models
 * (GET /api/providers/catalog -> onboard_defaults) and RETURN them. No module
 * state: the panels hold the result in React state and hand it to the pickers
 * in lib/agentFramework as an explicit argument.
 */
import { api } from '@/lib/api'
import type { OnboardDefaults } from '@/lib/agentFramework'

export interface OnboardDefaultsResult {
  /** false when the table could not be obtained; ``defaults`` is then ``{}``. */
  ok: boolean
  defaults: OnboardDefaults
}

/**
 * Normalise the response field into a fresh table, or null when it is not an
 * object (a missing / malformed field is a failure, never an empty table).
 * Entries that are not objects, and non-string / empty model ids, are dropped.
 */
function parseOnboardDefaults(raw: unknown): OnboardDefaults | null {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null
  const out: OnboardDefaults = {}
  for (const [source, entry] of Object.entries(raw)) {
    if (typeof entry !== 'object' || entry === null) continue
    const { agent, helper } = entry as Record<string, unknown>
    const row: { agent?: string; helper?: string } = {}
    if (typeof agent === 'string' && agent) row.agent = agent
    if (typeof helper === 'string' && helper) row.helper = helper
    if (row.agent || row.helper) out[source] = row
  }
  return out
}

/**
 * Never rejects (it sits in the panels' ``Promise.all``; a rejection would sink
 * the whole load) and never hides a failure: on a failed request, a
 * non-success response or an invalid field it ``console.warn``s and returns
 * ``ok: false`` so the caller can report the load as failed.
 */
export async function refreshOnboardDefaults(): Promise<OnboardDefaultsResult> {
  try {
    const res = await api.getProviderCatalog()
    if (!res.success) throw new Error('GET /api/providers/catalog reported failure')
    const defaults = parseOnboardDefaults(res.onboard_defaults)
    if (!defaults) throw new Error('GET /api/providers/catalog returned no usable onboard_defaults')
    return { ok: true, defaults }
  } catch (e) {
    console.warn('onboard defaults unavailable', e)
    return { ok: false, defaults: {} }
  }
}
