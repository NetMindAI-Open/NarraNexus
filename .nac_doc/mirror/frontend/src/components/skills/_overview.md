---
code_dir: frontend/src/components/skills/
last_verified: 2026-04-10
---

# skills/ — Skill installation, management, and the "Study" feature

Skills are agent capability bundles — essentially a directory of docs,
prompts, and optionally executable scripts that the agent can learn and use.
This panel lets users install, enable/disable, configure env vars, trigger
"Study" (agent reads the skill's docs), and remove skills.

## Component tree

```
SkillsPanel
  ├── InstallDialog          ← modal for GitHub URL or .zip upload
  ├── EnvConfigDialog        ← modal for secret env var configuration (inline in SkillsPanel)
  └── SkillCard (×n)         ← one card per installed skill
```

## Data layer

All mutations use TanStack Query hooks from `@/hooks/useSkills`:
`useSkillsList`, `useInstallFromGithub`, `useInstallFromZip`,
`useToggleSkill`, `useRemoveSkill`, `useStudySkill`, `useStudyStatus`.

`useStudyStatus` polls the backend while a study is in progress and updates
the skill's `study_status` field in the cache. `SkillsPanel` auto-detects
in-progress studies on page load (looks for `study_status === 'studying'` in
the list) so polling resumes after a browser refresh.

## Gotchas

`InstallDialog` validates that the URL matches `github.com/...` or
`github:user/repo` format. Local paths are not supported.
