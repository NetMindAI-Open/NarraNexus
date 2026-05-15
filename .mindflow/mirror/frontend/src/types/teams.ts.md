---
code_file: frontend/src/types/teams.ts
last_verified: 2026-05-15
stub: false
---

# teams.ts — Frontend types for subproject 1 + 2

把 backend 的 Team / TeamMember / Bundle 各种 request/response shape 镜像成 TypeScript interface。新增类型时同步两端：先在 backend 的 Pydantic 改，再 mirror 到这里，避免运行时字段名 drift。

## 2026-05-15 — bundle 1.1 additions

- `BundleExportRequest.mcp_selection?: Record<agent_id, mcp_id[]>` —— opt-in 默认无（null / {} = 不打包 MCP）
- `BundleExportRequest.artifact_selection?: Record<agent_id, artifact_id[]>` —— 默认 null = 全收
- `BundleArtifactPreview` / `BundleMcpPreview` —— `/api/bundle/export/preview/{artifacts,mcps}` 的返回体
- `BundleConfirmResponse.artifacts_created` / `mcp_urls_created` —— importer 直接落库的两个计数（1.1+）
