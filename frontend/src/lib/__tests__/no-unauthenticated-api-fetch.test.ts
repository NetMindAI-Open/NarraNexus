/**
 * @file_name: no-unauthenticated-api-fetch.test.ts
 * @description: Components must not call /api/ with a raw fetch().
 *
 * Local mode rejects any /api/ request without identity headers. `lib/api`
 * attaches them; a bare `fetch('/api/…')` does not — and the 401 that follows
 * arrives as a thrown/odd body that call sites routinely mistake for a normal
 * negative answer.
 *
 * That is not hypothetical. On 2026-09-22 the browser settings pane used a raw
 * fetch, and its 401 surfaced as "browser not installed" next to an install
 * button that did nothing when clicked. Both the status and the action were
 * being rejected; neither said so.
 *
 * A per-component test cannot catch this — each one stubs the fetch it is
 * testing. Only a scan over the source can, and only a scan covers files that
 * have not been written yet. Same reasoning as
 * `lib/__tests__/no-native-dialogs.test.ts`.
 */
import { describe, expect, test } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

// vitest runs with the frontend package as cwd; resolving from import.meta.url
// gave '/src' here, which silently scanned nothing.
const SRC = join(process.cwd(), 'src');

/**
 * Files allowed to talk to /api/ without the client, each for a stated reason.
 * Adding a line here is a decision about identity handling, not a formality.
 */
const ALLOWED = new Set([
  // The client itself — this is where the headers get attached.
  'lib/api.ts',
  // Probes the 401 handler it serves; going through request<T> would recurse.
  'lib/sessionGuard.ts',
  // Attaches identity itself via `this._authHeaders()` before each call, so
  // it is authenticated — just not through the client.
  'lib/platform.ts',
  // NOT an endorsement: this one is BROKEN and tracked. It posts to
  // `/api/agents/{id}/messages`, which is not a registered route, with no
  // identity headers, and swallows the result with `.catch(() => {})` — so
  // the "back up this skill" action silently never happens. Listed here only
  // so this scan can protect the rest of the app meanwhile; remove the entry
  // when the backup action is fixed.
  'pages/BundleExportPage.tsx',
]);

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === '__tests__' || name === 'dist') continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) sourceFiles(full, out);
    else if (/\.(ts|tsx)$/.test(name) && !name.endsWith('.d.ts')) out.push(full);
  }
  return out;
}

describe('no unauthenticated /api/ fetches', () => {
  test('every /api/ call goes through lib/api', () => {
    const offenders: string[] = [];

    for (const file of sourceFiles(SRC)) {
      const rel = relative(SRC, file);
      if (ALLOWED.has(rel)) continue;
      const body = readFileSync(file, 'utf8');

      // `fetch(` … `/api/` on the same call. Deliberately loose: the point is
      // to make the author justify the line, not to parse TypeScript.
      for (const match of body.matchAll(/fetch\s*\(([^)]*)\)/g)) {
        if (match[1].includes('/api/')) {
          offenders.push(`${rel}: ${match[0].slice(0, 80)}`);
        }
      }
    }

    expect(
      offenders,
      'raw fetch() to /api/ misses the identity headers local mode requires; ' +
        'use `api.*` (lib/api.ts) or add an explicit exemption with a reason',
    ).toEqual([]);
  });
});
