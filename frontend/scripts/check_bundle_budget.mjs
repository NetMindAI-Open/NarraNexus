#!/usr/bin/env node
// Bundle budget gate (spec §12/§13): the first-load chunk set of dist/index.html must stay
// under frontend/budget.json and contain no plugin code. Runs in CI after `npm run build`.
import { readFileSync, statSync, readdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const budget = JSON.parse(readFileSync(resolve(root, 'budget.json'), 'utf8'));
const dist = resolve(root, 'dist');
const html = readFileSync(resolve(dist, 'index.html'), 'utf8');
const refs = [...html.matchAll(/(?:src|href)="\/?(assets\/[^"]+)"/g)].map((m) => m[1]);
const sizes = refs.map((r) => [r, statSync(resolve(dist, r)).size]);
const initial = sizes.reduce((a, [, s]) => a + s, 0);
const entry = sizes.find(([r]) => /assets\/index-[^/]+\.js$/.test(r));
const problems = [];
if (initial > budget.initial_total_bytes) problems.push(`initial set ${initial} > ${budget.initial_total_bytes}`);
if (entry && entry[1] > budget.entry_script_bytes) problems.push(`entry ${entry[0]} ${entry[1]} > ${budget.entry_script_bytes}`);
for (const [r] of sizes) for (const p of budget.initial_forbidden_chunk_patterns) if (r.includes(p)) problems.push(`initial set contains ${r} (forbidden: ${p})`);
// Largest LAZY chunk (the entry is budgeted separately above).
const largest = readdirSync(resolve(dist, 'assets')).filter((f) => f.endsWith('.js') && !/^index-/.test(f)).map((f) => [f, statSync(resolve(dist, 'assets', f)).size]).sort((a, b) => b[1] - a[1])[0];
if (largest && largest[1] > budget.largest_lazy_chunk_bytes) problems.push(`largest chunk ${largest[0]} ${largest[1]} > ${budget.largest_lazy_chunk_bytes}`);
console.log(`bundle budget: initial ${initial} bytes over ${sizes.length} files; entry ${entry ? entry[1] : 'n/a'}; largest ${largest ? largest.join(' ') : 'n/a'}`);
if (problems.length) {
  for (const p of problems) console.error(`- ${p}`);
  process.exit(1);
}
