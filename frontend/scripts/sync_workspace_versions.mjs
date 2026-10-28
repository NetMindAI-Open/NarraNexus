#!/usr/bin/env node
// Copies `frontend/package.json`'s "version" into the three publishable workspace
// packages (sdk, ui-kit, chat-widget). These are separate npm packages, but they ship
// alongside the app and describe its HostAPI / embedding surface at that release —
// letting them drift from the app version (and from each other) is how `@narranexus/sdk`
// on npm ends up describing a HostAPI shape from a different release than its version
// number suggests. Run this as part of bumping the release version anchors (see the
// deploy repo's release SOP); it is NOT run automatically by `npm install` or CI.
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const rootPkg = JSON.parse(readFileSync(resolve(root, 'package.json'), 'utf8'));
const version = rootPkg.version;
if (typeof version !== 'string' || !version) {
  console.error('sync_workspace_versions: frontend/package.json has no "version"');
  process.exit(1);
}

const targets = ['packages/sdk/package.json', 'packages/ui-kit/package.json', 'packages/chat-widget/package.json'];
let changed = 0;
for (const rel of targets) {
  const path = resolve(root, rel);
  const raw = readFileSync(path, 'utf8');
  const pkg = JSON.parse(raw);
  if (pkg.version === version) continue;
  pkg.version = version;
  // Preserve trailing newline convention (JSON.stringify + 2-space indent, matching the
  // existing files) and a final newline.
  writeFileSync(path, `${JSON.stringify(pkg, null, 2)}\n`);
  changed += 1;
  console.log(`sync_workspace_versions: ${rel} -> ${version}`);
}
console.log(changed ? `sync_workspace_versions: updated ${changed} package(s) to ${version}` : `sync_workspace_versions: already at ${version}`);
