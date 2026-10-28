/**
 * The picker's sub-label has to be right: it is what tells a user whether a
 * provider was set up with a pasted key or a CLI sign-in. Deriving it from
 * `auth_type` would collapse claude_oauth and a pasted bearer token into the
 * same label, so these tests pin the mapping to `source`.
 */
import { describe, test, expect } from 'vitest';
import { deriveProviderRows } from '../providerRows';

describe('deriveProviderRows', () => {
  test('labels OAuth driver sources as CLI sign-in', () => {
    const rows = deriveProviderRows({
      a: { provider_id: 'p1', name: 'Claude Code (OAuth)', source: 'claude_oauth', auth_type: 'bearer_token' },
      b: { provider_id: 'p2', name: 'Codex (OAuth)', source: 'codex_oauth', auth_type: 'bearer_token' },
    });
    expect(rows.map((r) => r.access)).toEqual(['cli', 'cli']);
  });

  test('labels key-based sources as API Key even when auth_type is a bearer token', () => {
    const rows = deriveProviderRows({
      a: { provider_id: 'p1', name: 'NetMind', source: 'netmind', auth_type: 'bearer_token' },
      b: { provider_id: 'p2', name: 'Yunwu', source: 'yunwu', auth_type: 'api_key' },
    });
    expect(rows.map((r) => r.access)).toEqual(['api_key', 'api_key']);
  });

  test('is case- and whitespace-insensitive on source', () => {
    const rows = deriveProviderRows({
      a: { provider_id: 'p1', source: ' Claude_OAuth ' },
    });
    expect(rows[0].access).toBe('cli');
  });

  test('preserves server ordering rather than re-sorting', () => {
    const rows = deriveProviderRows({
      z: { provider_id: 'zeta', name: 'Zeta', source: 'netmind' },
      a: { provider_id: 'alpha', name: 'Alpha', source: 'netmind' },
    });
    expect(rows.map((r) => r.id)).toEqual(['zeta', 'alpha']);
  });

  test('falls back to the id when the name is missing or blank', () => {
    const rows = deriveProviderRows({
      a: { provider_id: 'p1', source: 'netmind' },
      b: { provider_id: 'p2', name: '   ', source: 'netmind' },
    });
    expect(rows.map((r) => r.name)).toEqual(['p1', 'p2']);
  });

  test('drops malformed entries instead of rendering half-empty rows', () => {
    const rows = deriveProviderRows({
      ok: { provider_id: 'p1', name: 'Fine', source: 'netmind' },
      noId: { name: 'No id', source: 'netmind' },
      blankId: { provider_id: '  ', name: 'Blank id' },
      notObject: 'nope',
      nullish: null,
    });
    expect(rows.map((r) => r.id)).toEqual(['p1']);
  });

  test('empty and absent maps give an empty list', () => {
    expect(deriveProviderRows({})).toEqual([]);
    expect(deriveProviderRows(undefined)).toEqual([]);
    expect(deriveProviderRows(null)).toEqual([]);
  });
});
