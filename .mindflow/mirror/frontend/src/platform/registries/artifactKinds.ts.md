---
code_file: frontend/src/platform/registries/artifactKinds.ts
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — depcruise 例外的口径更正

`src/types/**` 不是「无运行时代码」（`artifact.ts` 导出函数）；例外只放行 type-only 引用，并加了一条
`registries-take-only-types-from-src-types` 规则把值 import 挡住。

# artifactKinds.ts — artifact kind 注册表

## 2026-09-07（批 1 三轮复审移植）— 新建：第 17 个注册表

批 1 复审指出 `registerArtifactKind` 是与 `Registry<T>` 语义不同的第二套注册表（无 owner、无冲突检测、无订阅、
dispose 语义相反），且不在插件唯一入口上。现在 artifact kind 与其它 16 个 UI 扩展点同形：`ARTIFACT_KINDS`
是 `Registry<KindDescriptor>`，进 `REGISTRIES` 后自动出现在 `HostAPI.registries.artifactKinds`（owner 固定为插件 id、
随插件 deactivate 释放）。**策略变化**：插件不能再静默覆盖壳的内置 kind（跨 owner 同 id 抛 `RegistryConflictError`），
只能新增 kind——旧栈式「覆盖后 dispose 恢复内置」语义随之退役，这是有意的。
描述符词表（`EditSurface`/`SaveMode`/`PreviewStrategy`/`KindDescriptor`）原样从 `components/artifacts/kindRegistry.ts`
搬来；`RendererComponent` 放宽为 lazy 或普通组件（插件 bundle 通常不是 lazy）。注册期校验
`validateKindDescriptor`（M-11 的 validating registry 模式）：saveMode 恰在 editSurface≠none 时存在、placeholder
预览必须带 i18n key——过去是测试里的断言，现在是插件注册时就会撞上的规则。
依赖 `@/types/artifact` 的 `Artifact` 类型：depcruise 的 `registries-are-pure` 规则为此放行 `src/types/**`（纯类型，无运行时）。
