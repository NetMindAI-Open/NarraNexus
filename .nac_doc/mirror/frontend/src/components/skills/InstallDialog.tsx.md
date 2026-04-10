---
code_file: frontend/src/components/skills/InstallDialog.tsx
last_verified: 2026-04-10
---

# InstallDialog.tsx — Modal for installing skills from GitHub or zip file

Renders as a fixed overlay when `mode` is `'github'` or `'zip'`; renders
nothing when `mode` is `null`. Controlled entirely by `SkillsPanel`.

The GitHub mode validates the URL format (must start with `https://github.com`
or `github:user/repo`). The zip mode validates the `.zip` extension before
calling `onInstall`.

A security warning is always shown: skills can contain docs and scripts that
the agent reads/uses.

## Upstream / downstream

- **Used by:** `SkillsPanel` only
- **Calls back:** `onInstall({ url?, branch?, file? })` — `SkillsPanel`
  routes to the appropriate TanStack Query mutation

## Gotchas

State (url, branch, file, error) is local to the dialog. If the user
dismisses without installing and then re-opens, all fields reset. This is
intentional — no drafts are saved.
