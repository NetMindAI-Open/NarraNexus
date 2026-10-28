---
code_file: frontend/test-setup.ts
last_verified: 2026-09-07
stub: false
---

## 2026-09-07（批 1 三轮复审移植）— 全局导入 `platform/builtin` 的含义

注册表是模块单例：每个前端测试文件都带着内置注册。测试里再注册一个与内置同 id 的条目会抛
`RegistryConflictError`（除非 `replace`），errorSink 也是同一个单例——用过 `onUiError/reportUiError` 的测试要在
`afterEach` 里 `resetErrorSink()`。vitest 按文件隔离模块，跨文件不串。
# test-setup.ts — 让 jsdom 足够像浏览器，测试才能测真东西

## 2026-09-03（插件平台批 1）— 同步 import `src/platform/builtin`

应用里由 `main.tsx` 在首帧前注册壳的页面/侧栏/面板；渲染壳的测试需要同样的注册，所以 setup 也
side-effect import 一次（设置分区由 `SettingsPage` 自己的 chunk 注册，不在这里）。
`src/platform/__tests__/bootstrap.test.ts` 钉住这一行与 `main.tsx` 的顺序。

## 为什么存在

vitest 的默认环境是 jsdom，而 jsdom 不是浏览器：它缺一些**在模块加载期就会被调用**
的 API。缺的那一个不会给你一条清晰的报错，它会在任何测试代码跑起来之前就把整个测试
文件炸掉——于是问题看起来出在被测组件上，其实出在环境上。

这个文件就是那份补丁清单。每一条都对应一次真实的、诊断成本远高于修复成本的失败：

- **`window.matchMedia`**：themeStore 在模块初始化时就调用它，而 Markdown 会传递性
  地加载 themeStore。任何 import 了任何碰主题系统的东西的测试，都会在第一行测试代码
  之前失败。
- **`localStorage`**：Node 22 带了一个实验性的内置 `localStorage`（由
  `--localstorage-file` 开关控制）。这个开关在没有合法路径时到达 runner，会装上一个
  **坏掉的**全局对象并**遮蔽 jsdom 自己的那个**——`localStorage.clear` 于是"不是一个
  函数"，每一个带 `beforeEach(localStorage.clear)` 的测试在跑起来之前就死了。这里
  的判据是"缺失**或不完整**"，所以套件不依赖具体的 Node 构建。
- **`Element.prototype.scrollIntoView`**（2026-08-14）：jsdom 没有实现。任何渲染了
  至少一条消息的 transcript 都会命中"跟随到底部"的 effect 并在 passive effect 里抛
  异常——vitest 把它报成**测试通过之后**的 unhandled error，于是套件同时是绿的和吵的，
  而且真正的报错藏在四十行 React 栈里。

  空实现是**诚实的**桩：这个 effect 做的事在 jsdom 里本来就不可观测；而"该不该滚"
  这个**决定**是单独可测的，钉在 `lib/__tests__/scrollStickiness.test.ts`
  （见 [[scrollStickiness.ts]]）。

- **`IntersectionObserver` / `ResizeObserver`**(2026-08-19):jsdom 两个都没有。
  md 块编辑器(Milkdown Crepe)在 mount 时就构造它们——代码块的可视区
  懒渲染用前者,工具条定位用后者;缺任何一个,所有挂 MarkdownRenderer
  的测试在组件渲染前整文件炸掉。noop 桩是诚实的:layout 回调在 jsdom
  里本来不可观测,观察不到任何东西的 observer 与不存在的 observer 在
  jsdom 语义下等价。

## 一个不是补丁的东西

`import './src/i18n'` 不是兼容垫片，是刻意的选择：组件测试里 `useTranslation()`
解析出**真实的英文字符串**，而不是原始的 key。这样"这个按钮上写的是什么"是可断言的，
并且一个漏掉的 i18n key 会在测试里表现为文案不见了，而不是悄悄显示成一个 key。
