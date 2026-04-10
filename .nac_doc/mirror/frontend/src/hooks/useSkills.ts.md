---
code_file: frontend/src/hooks/useSkills.ts
last_verified: 2026-04-10
stub: false
---

# useSkills.ts — TanStack Query hooks for Skills CRUD

## Why it exists

Skills management (list, install, toggle, remove, study) is a self-contained CRUD domain with server-state caching needs that fit TanStack Query better than Zustand. Skills data is not shared across panels and does not need to be accessible outside the Skills panel, so it does not belong in `preloadStore`. Using TanStack Query here also gives automatic stale-while-revalidate, deduplication, and mutation-to-invalidation patterns without hand-rolling them.

## Upstream / Downstream

All hooks read `agentId` and `userId` from `useConfigStore`. All mutations call `api.*Skills*` endpoints and invalidate the `[SKILLS_KEY]` query family on success. `useStudyStatus` additionally invalidates the full skills list when a study job completes or fails.

Consumed exclusively by `SkillsPanel.tsx`, `SkillCard.tsx`, and `InstallDialog.tsx`. Not re-exported through `hooks/index.ts` — imported directly from `@/hooks/useSkills`.

## Design decisions

**Hierarchical query key.** All skills queries use `[SKILLS_KEY, agentId, userId, ...]` so switching agents automatically invalidates the cache. `qc.invalidateQueries({ queryKey: [SKILLS_KEY] })` in mutations wipes all skills variants regardless of filter.

**`useStudyStatus` self-cancels.** The `refetchInterval` callback inspects the latest query data: if study is `completed` or `failed`, it returns `false` to stop polling. When either terminal state arrives, it also invalidates the full skills list. This avoids a separate effect to stop polling.

**`enabled` guard on all queries.** Every `useQuery` call includes `enabled: !!agentId && !!userId`. Without this, the hook fires before login completes and generates 400/401 errors.

**Mutations do not optimistically update.** Rejected because the backend-side skill file system operations (unzip, git clone) are not reversible or predictable in their outcome. Better to refetch from source of truth.

## Gotchas

**`agentId!` non-null assertion.** All `mutationFn` calls use `agentId!` and `userId!`. The `enabled` guard on `useSkillsList` prevents the query from firing when they are null, but mutations have no such guard — if a component calls `useInstallFromGithub().mutate(...)` before `agentId` is set, the assertion will pass at runtime (TypeScript type system) but the value will be an empty string, causing a backend 404.

**`useStudyStatus` polling interval type annotation is verbose.** The `refetchInterval` callback has an explicit type annotation for the `query` parameter because TypeScript cannot infer the data shape through the generic. If the `SkillStudyResponse` type changes, this annotation needs updating.

**Not in `hooks/index.ts` barrel.** Adding a new consumer outside `SkillsPanel` requires knowing to import from `@/hooks/useSkills` rather than `@/hooks`.
