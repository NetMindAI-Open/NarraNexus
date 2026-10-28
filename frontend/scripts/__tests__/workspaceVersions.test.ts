/**
 * @file_name: workspaceVersions.test.ts
 * @author: Bin Liang
 * @date: 2026-09-07
 * @description: The three publishable workspace packages' versions must match `frontend/package.json` — a release that bumps the app version without running `npm run sync:versions` (scripts/sync_workspace_versions.mjs) leaves them silently behind.
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const version = (rel: string): string => (JSON.parse(readFileSync(resolve(root, rel), 'utf8')) as { version: string }).version;

describe('workspace package versions', () => {
  it('sdk / ui-kit / chat-widget stay in lockstep with the app version', () => {
    const app = version('package.json');
    expect(version('packages/sdk/package.json')).toBe(app);
    expect(version('packages/ui-kit/package.json')).toBe(app);
    expect(version('packages/chat-widget/package.json')).toBe(app);
  });

  it('chat-widget pins ui-kit to a real range, not "*" (a published `*` resolves to any npm version, not the one built alongside it)', () => {
    const pkg = JSON.parse(readFileSync(resolve(root, 'packages/chat-widget/package.json'), 'utf8')) as { dependencies: Record<string, string> };
    expect(pkg.dependencies['@narranexus/ui-kit']).not.toBe('*');
    expect(pkg.dependencies['@narranexus/ui-kit']).toMatch(/^\^?\d+\.\d+\.\d+$/);
  });
});
