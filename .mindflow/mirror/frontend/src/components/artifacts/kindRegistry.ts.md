---
code_file: frontend/src/components/artifacts/kindRegistry.ts
last_verified: 2026-09-07
stub: false
---

## 2026-09-07（批 1 三轮复审移植）— 只剩内置表 + `downloadExtFor`

`KIND_REGISTRY` 与 `registerArtifactKind`（自带注册栈）删除；本文件是 `BUILTIN_ARTIFACT_KINDS`（对 `BuiltinArtifactKind`
穷举的 Record）+ `downloadExtFor`（改读 `ARTIFACT_KINDS.get`）。词表类型从 `platform/registries/artifactKinds.ts`
re-export 以保留既有 import 路径。内置表由 `platform/builtin.ts` 注册进 `ARTIFACT_KINDS`（owner `builtin.ui`），
消费方一律查注册表，不再直接读本表——插件 kind 与内置 kind 同一条路径解析。

# kindRegistry.ts — kind 能力注册表(单一事实源)

## 2026-09-03 — `registerArtifactKind`：插件可加渲染器

`KIND_REGISTRY` 类型改为 `Record<BuiltinArtifactKind, KindDescriptor> & Partial<Record<string, KindDescriptor>>`
：内置键仍穷举，任意字符串键读出来是 `KindDescriptor | undefined`，消费方必须 `?.`（原来的
`Record<string, ...>` 让未知键假装总存在）。`registerArtifactKind` 每 kind 维护一个注册栈，内置描述符是
隐式栈底：撤销函数只移除自己那一项，当前值取栈顶或内置——插件按任意顺序卸载都不会把别人的描述符
打掉，也不会让已撤销的描述符"复活"（首版的 previous 快照法会）。测试 `registerArtifactKind` 三段。

## 2026-08-21 — `downloadExt` 变 optional + 新增 `downloadExtFor`(深圳复测 .bin bug)

office-live 与 x-url 原来写死 `downloadExt: 'bin'`——但 office-live 一个
kind 盖 pptx/docx/xlsx 三种文件,**不存在**单一静态扩展名,'bin' 就是
「pptx 下载得 .bin」的直接来源。改法:`downloadExt` 变 optional
(缺席 = 该 kind 无单一自然扩展名),消费方一律走 `downloadExtFor(artifact)`
——静态 ext 优先(kind 描述 entry 语义,.markdown 指针仍下成 .md),
缺则取 `file_path` 扩展名(alnum≤16 净化,它要进文件名),最后 'bin'。
钉在 [[__tests__/kindRegistry.test.ts]](含敌意扩展名用例)。

## 为什么存在

此前 kind 知识散落四处:ArtifactRenderer 的渲染表、ArtifactPreviewCard
的 `kind ===` 链、ArtifactDownloadMenu 的 ext 映射+isChart、
ArtifactsSection 的 label 映射。加一个 kind(或一种能力,如编辑)要猎遍
全部。现在每个消费者查一个描述符;新 kind = 一行描述符 + 一个 renderer
文件。

## 字段与消费方

| 字段 | 消费方 | 状态 |
|---|---|---|
| renderer | ArtifactRenderer | 活跃 |
| preview / previewPlaceholderKey | ArtifactPreviewCard | 活跃 |
| downloadExt / chartImageExport | ArtifactDownloadMenu | 活跃 |
| label | ArtifactsSection(缺省回退原始 kind 串) | 活跃 |
| editSurface / saveMode | 编辑外壳(C2~C4 逐步消费) | 声明先行 |
| selectionToAI | 选区→AI 通道(v1.5) | 声明先行 |

**声明先行是有意的**:office 编辑 spec 与 v1.5 不必再动表结构。

## editSurface 语义(无模式框架,2026-08-19 设计定稿)

**全系统没有 View/Edit toggle**——渲染面本身在 kind 允许处落光标:
block-editor(md,渲染即编辑器)/ per-element(html,点进元素失焦提交)/
resident-editor(csv,源即渲染常驻可编辑)/ office-watch(officecli
命令翻译)/ none(只读,改动走 AI)。saveMode 恰在 editSurface=none 时
为 null——这条不变量被 [[__tests__/kindRegistry.test.ts]] 钉死。

## 坑

- `ArtifactKind` 联合里没有 text/plain 或代码类 kind——常驻编辑器目前
  只有 csv 一员;将来加 code kind 就是注册表加行。
- lazy import 全部迁到本文件(模块级、单处),React.lazy 按 import 调用
  点 memoise——列与 zoom 弹窗仍共享 chunk,勿在别处再写同名 lazy。
- label 故意可缺省:office-live/x-url 沿用原始 kind 串显示(行为保持
  的重构约束);要给它们人话名先确认设置页文案预期。
