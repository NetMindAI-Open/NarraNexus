/**
 * Embedding removal contract test.
 *
 * Verifies that every frontend file listed in the spec contains zero
 * references to embedding-related identifiers after the cleanup pass.
 *
 * "TDD Red" before the edits: the files still contain embedding code, so
 * several assertions below will fail. After cleanup the test must be green.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, test, expect } from 'vitest';

const root = resolve(__dirname, '../../');

function read(rel: string): string {
  return readFileSync(resolve(root, rel), 'utf-8');
}

/** Returns all embedding-related tokens found in the content. */
function embeddingTokens(content: string): string[] {
  const patterns = [
    /\bEmbedding[A-Z]/g,       // EmbeddingStatus, EmbeddingBanner, EmbeddingStatusData, etc.
    /\bembedding_compat\b/g,   // preflight.embedding_compat
    /\membedding_provider\b/g, // BundleExportRequest field
    /\membedding_model\b/g,
    /\membedding_dim\b/g,
    /getEmbeddingStatus/g,
    /rebuildEmbeddings/g,
    /mockEmbeddingStatus/g,
    /\bembedding['"]:\s*\{/g,  // embedding?: { ... } in types
  ];
  const found: string[] = [];
  for (const p of patterns) {
    const m = content.match(p);
    if (m) found.push(...m);
  }
  return [...new Set(found)];
}

describe('no embedding code in frontend', () => {
  const files: [string, string][] = [
    ['SettingsModal', 'src/components/settings/SettingsModal.tsx'],
    ['SettingsPage', 'src/pages/SettingsPage.tsx'],
    ['api.ts', 'src/lib/api.ts'],
    ['mock/index.ts', 'src/lib/mock/index.ts'],
    ['mock/fixtures.ts', 'src/lib/mock/fixtures.ts'],
    ['types/api.ts', 'src/types/api.ts'],
    ['types/teams.ts', 'src/types/teams.ts'],
    ['BundleImportPage', 'src/pages/BundleImportPage.tsx'],
    ['ProviderSettings', 'src/components/settings/ProviderSettings.tsx'],
  ];

  for (const [label, rel] of files) {
    test(`${label} has no embedding references`, () => {
      const content = read(rel);
      const found = embeddingTokens(content);
      expect(found, `Found in ${rel}: ${found.join(', ')}`).toHaveLength(0);
    });
  }
});
