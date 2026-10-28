/**
 * @file_name: providerRows.ts
 * @author: NetMind.AI
 * @date: 2026-09-03
 * @description: Turns /api/providers' loosely-typed provider map into the
 * rows the creation studio's picker renders.
 *
 * Kept separate from the modal so the mapping is testable without mounting
 * anything. `api.getProviders()` is typed `Record<string, unknown>` on
 * purpose (see api.ts) — the full schema had exactly one consumer until now.
 * The canonical row shape is `providersApi.ProviderRow`; this module narrows
 * it to the four things the picker draws and drops anything malformed rather
 * than rendering a half-empty row.
 *
 * The access label comes from `source`, NOT from `auth_type`. auth_type is
 * only 'api_key' | 'bearer_token' — it cannot tell a CLI OAuth session apart
 * from a pasted bearer token. `source` names the provider driver
 * (claude_oauth / codex_oauth / netmind / yunwu / …), which is exactly the
 * distinction the design's sub-label draws.
 */
import type { ProviderRow } from '@/lib/providersApi';

/** Sources whose credential comes from a CLI sign-in, not a pasted key. */
export const CLI_SIGN_IN_SOURCES = new Set(['claude_oauth', 'codex_oauth']);

/** What the picker needs per row. Narrower than providersApi.ProviderRow. */
export interface PickerRow {
  id: string;
  name: string;
  /** Drives the sub-label: 'cli' → "CLI sign-in", 'api_key' → "API Key". */
  access: 'api_key' | 'cli';
  /** Raw driver source (e.g. 'anthropic'). Not rendered today. */
  source: string;
  /** Server's own health verdict — drives the status dot, not our guess. */
  active: boolean;
}

/**
 * Map the provider record to picker rows, preserving the server's ordering.
 *
 * Order is deliberately NOT re-sorted: ProviderSettings lists providers in
 * the same `Object.values` order, and two surfaces listing the same
 * providers in different orders reads as a bug.
 */
export function deriveProviderRows(
  providers: Record<string, unknown> | undefined | null,
): PickerRow[] {
  if (!providers) return [];
  const rows: PickerRow[] = [];
  for (const entry of Object.values(providers)) {
    if (!entry || typeof entry !== 'object') continue;
    const rec = entry as Partial<ProviderRow>;
    const id = typeof rec.provider_id === 'string' ? rec.provider_id.trim() : '';
    if (!id) continue;
    const source = typeof rec.source === 'string' ? rec.source.trim() : '';
    const name = typeof rec.name === 'string' && rec.name.trim() ? rec.name.trim() : id;
    rows.push({
      id,
      name,
      source,
      access: CLI_SIGN_IN_SOURCES.has(source.toLowerCase()) ? 'cli' : 'api_key',
      // Absent is treated as healthy: a backend that predates the field, or
      // one that omits it, must not paint every provider as broken.
      active: rec.is_active !== false,
    });
  }
  return rows;
}
