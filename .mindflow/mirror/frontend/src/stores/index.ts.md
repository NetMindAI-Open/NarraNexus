---
code_file: frontend/src/stores/index.ts
last_verified: 2026-09-04
stub: false
---

# index.ts — Stores barrel export

## Why it exists

Provides a single import path `@/stores` for all store hooks and the `JOB_TEMPLATES` constant. Consumers write `import { useConfigStore, useChatStore } from '@/stores'` instead of knowing which file each store lives in.

## 2026-09-03

新增 `studioStore` 及其四个 selector / helper（`selectStudioOpen`、`selectStudioResumable`、
`selectRecommendations`、`isStudioOpen`）的再导出，见 [[studioStore.ts]]。

## Notes

`embeddingStore` is intentionally not re-exported here. `EmbeddingStatus.tsx` imports it directly from `./embeddingStore`. This is an oversight rather than a design decision — adding it to the barrel would be a safe one-line change.

`runtimeStore` is exported but `getApiBaseUrl` and `getWsBaseUrl` from that file are not re-exported here. They are imported directly by `api.ts` and `wsManager.ts` to keep the import path explicit and traceable.
