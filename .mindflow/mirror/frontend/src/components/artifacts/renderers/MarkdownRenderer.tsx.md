---
code_file: frontend/src/components/artifacts/renderers/MarkdownRenderer.tsx
last_verified: 2026-08-20
stub: false
---

## 2026-08-04 — 根节点必须保持 auto 高度、零滚动容器

云端 artifact 滑不动修复（Base recvpm05jsLg3o）定稿版：滚动归属在
**ArtifactRenderer 的 bounded 包装盒**（列内），或 ZoomModal 的外层
overflow-auto（弹窗内）——本渲染器根 div 保持 auto 高度且**不再带
overflow-auto**（原有的那个纵向从未生效过，是死代码）。第一版曾给根
div 加 `h-full` 自滚，review 打回：h-full 在弹窗里会对着 scale 层的
确定高度解析，把内容钳成一屏+嵌套滚动条。契约测试
[[../../__tests__/scrollContainment.test.tsx]] 钉死"根节点无 h-full、
无滚动容器"。

## 2026-05-27 — same Dismiss-loop fix as HtmlRenderer

`heal.attempt` now via `attemptRef`; load effect deps reduced to
`[url]`. See HtmlRenderer.tsx.md + useArtifactHeal.ts.md for the
shared bug story (modal stuck open after Dismiss, P0 2026-05-25).

## 2026-05-14 — drop `version` prop, fetch via `useArtifactRawUrl`

Renderer no longer takes a `version` prop. Uses `useArtifactRawUrl` for the
token-protected public URL; `fetchArtifactText` runs without an Authorization
header.

# MarkdownRenderer.tsx — Markdown artifact renderer

## Why it exists

Fetches a `text/markdown` artifact and renders it with ReactMarkdown + remark-gfm. Artifact Markdown is distinct from chat-bubble Markdown (`ui/Markdown.tsx`) in that the content arrives from a fetch rather than a prop string — hence a separate component.

## Upstream / Downstream

- **Used by**: `ArtifactColumn` via `React.lazy`, dispatched when `artifact.kind === 'text/markdown'`.
- **Calls**: `rawUrl()` from `@/types/artifact` for the fetch target.
- **Bundle**: Both this file and `ui/Markdown.tsx` import from `react-markdown` and `remark-gfm`, which land in the `vendor-markdown` manualChunk. No additional bundle cost once the chunk is loaded.

## Design decisions

**`markdown-content`** (I8, 2026-05-09) — replaced `prose prose-invert` with the app's own `.markdown-content` CSS class from `index.css`. The `prose-invert` Tailwind Typography class hardcoded a dark-theme assumption. The app styles Markdown via `.markdown-content` using CSS custom properties (see `index.css`), so both this renderer and `ui/Markdown.tsx` now use the same theme-aware class. Dropping `prose-invert` means theme changes in the CSS variables flow through automatically without touching the component.

**No rehype-raw.** Unlike `ui/Markdown.tsx` (which enables raw HTML for message bubbles that may contain agent-formatted HTML fragments), the artifact renderer intentionally omits `rehypeRaw` so that literal HTML tags in the Markdown are escaped rather than rendered. An agent that needs rendered HTML should emit a `text/html` artifact rendered by `HtmlRenderer` instead.

**Empty string initial state.** `setText` fires on each `version` change. Starting from `''` means the component renders a blank `<div>` during the fetch rather than stale content from the previous version. Acceptable flicker for the typical small-to-medium Markdown files agents emit.

## Gotchas

**Error handling (2026-05-08-r2).** The fetch chain now checks `r.ok` before calling
`r.text()`. On a non-2xx response it rejects with `Error("HTTP {status}")`, which the
`.catch` handler stores in an `error` state slot. When `error` is set, the component
renders `<div className="p-4 text-red-400">Failed to load: {error}</div>` instead of
the prose container, mirroring the pattern in `CsvRenderer`.

The `useEffect` fetch has no abort controller. If `version` changes quickly (e.g., the user flips through version history), multiple concurrent fetches may race. The last one to resolve wins, which is usually correct (monotonically increasing versions). For a more rigorous fix, add `AbortController` inside the effect — low priority given typical usage patterns.

**Empty body placeholder (M9, 2026-05-09)**: A `!text && !error` guard was added before the prose container render. A 200-OK response with an empty body now shows `"(empty markdown)"` instead of a blank panel, which would look like a load failure to the user.

## 2026-08-19 — md=块编辑器(Crepe),渲染面即编辑面

无模式框架:Milkdown Crepe 常驻,点哪打哪;防抖自动保存(2s 静默,
冲突挂起时暂停);状态机在 [[useArtifactEditor.ts]]。三道防丢:
①frontmatter 编辑器**看不到**(会被摧毁成分割线+标题),加载时切出、
保存时原样回接([[mdEditSafety.ts]]);②挂载后**探针**:对未编辑原文
serialize 一次,与原文做 AST 等价比较——结构丢失(实测:reference 式
链接被解成内联)→ 隐藏编辑器,回退 ReactMarkdown 只读+守卫横幅;
风格规范化(列表符号/表格分隔线)放行,首次真实保存落盘;③dirty 时
外部重载被 useArtifactEditor 跳过。
**关键机制**:docBase(render 期状态调整,非 effect)= 面所挂载的文档
基;打字不换 docBase(零重挂),干净态外部刷新/冲突弃稿才换(key 重挂
Crepe)。Crepe 回调经 ref 读最新 state(effect 内赋值,规避
react-hooks/refs)。`.markdown-content` 类保留在两种面外壳上——
scrollContainment 契约测试认它。
**字节级往返已被 spike 证伪**(remark 系必然规范化风格),守卫从
「字节无损」改判「语义无损」——此偏离已记录待 Owner 复核。

## 2026-08-20 — 窄列排版修复 + 依赖归位

①Crepe 主题 reset 写死 `.ProseMirror{padding:60px 120px}`(整页编辑器
尺寸),artifact 列 ~340px 下内容区只剩 ~100px → 一行挤一两个字(zoom
弹窗宽,故看着正常)。修复=index.css 里 `.markdown-content .milkdown
.ProseMirror{padding:16px 16px 16px 48px}` 作用域覆盖——**左侧 48px 是
给 Crepe 块拖拽手柄(+/⋮⋮)的**,它渲染在左 padding 带,压小会盖字;
宿主 div 的 p-4 同步移除(padding 单一归属)。
②`@milkdown/crepe` 当时 npm i 跑错目录装到了 worktree 根(package.json
根本没记上,本机靠 Node 向上解析侥幸能跑)——已正式装进
frontend/package.json,根目录游离 node_modules 已删。新克隆/CI/DMG
构建自此才真正可用。

## 2026-08-20 — 守卫接管写路径 + CRLF + 行尾政策(review #334 I6/I7)

①**写路径门**:markdownUpdated 回调开头 `editableRef.current !== true`
即 return——`hidden` 只是布局属性,不再顶替业务守卫;②**探测期
readonly**:crepe.setReadonly(true) 到判决出炉,探测窗内根本产生不了
输入 ⇒「探测期击键落盘」「探测被用户输入污染」两个竞态整类消灭,
判决通过才解锁;③**行尾政策(显式决定)**:被编辑过的文档保存后
**全文 LF**——Crepe 的 body 恒 LF,frontmatter 保存时 CRLF→LF 归一,
不写混合行尾文件。CRLF 文档的 frontmatter 切分见 [[mdEditSafety.ts]]
(切分本身保字节,归一只发生在保存拼接)。风格级归一在损耗契约之内。
