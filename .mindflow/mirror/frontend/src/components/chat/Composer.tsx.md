---
code_file: frontend/src/components/chat/Composer.tsx
last_verified: 2026-09-04
stub: false
---

## 2026-08-24 — `trailingSlots` prop:右侧内边距随按钮数走

新增可选 prop `trailingSlots?: 1 | 2`(默认 1)。textarea 右侧压着 ChatPanel 绝对定位的动作键,
文本必须让位不能滑到键底下:1 颗键(平时的发送键,或流式态单独的 Stop)→ `pr-12`(8–44px);
steerable 运行中并排 2 颗(Stop @ `right-2` + steer 发送键 @ `right-12`,各 `w-9`,占 8–84px)→ `pr-24`(96px)。
ChatPanel 持有按钮,故由它在 `isStreaming && currentSteerable` 时传 `2`、其余传 `1`。布局数字
(`pr-*` ↔ `right-*`+`w-*`)本来分散在两文件里彼此耦合;这个 prop 把「留多宽」收敛成一个显式契约,
下次往这排加键改这一处即可。className 从常量串改为 `cn(...)` 拼接。

## 2026-08-19 — 输入框随内容增高

此前 `rows=1` + max-h-160 但**没有任何增高机制**,多行输入永远挤在一行高度
里滚。现在 `resizeToContent`(唯一实现)= 塌回 auto → 无条件写回
scrollHeight+边框(border-box 下 height 含边框而 scrollHeight 不含,
差值取 offsetHeight−clientHeight,必须在塌回 auto 之后读)。防自激在
**观察侧**:RO 回调只在 `contentRect.width` 变化时才重算——本回调只写
高度,按宽度过滤天然阻断回环(lastWidth 初值 −1 保证 observe 的首次投递
必跑)。两个触发面:useLayoutEffect 跟 `text`(程序性 setText/clear/
草稿恢复同路),ResizeObserver 跟宽度(拖抽屉/钉选/折叠侧栏/窗口 resize
都会改折行数)。CSS `max-h-[min(320px,35vh)]` 封顶,
超过后内滚。测试:composerAutosize.test.tsx。

## 2026-08-06 — 输入框浅底 + focus 边框加深

Owner 确认输入栏惯例:字段是卡面上**最浅**的面,focus 用边框加深表达。
composer 的 Textarea 覆盖 bg 为 --nm-card(白),并**移除**原先把
hover/focus 边框钉回 hairline 的覆盖 — 恢复 Textarea 基类行为
(hover→border-strong,focus→nm-ink)。nx-composer-input 的 outline
抑制不变(焦点信号只走边框)。渲染隔离契约不动。

## 2026-06-20 — ComposerHandle gained setText (suggested-prompt fill)

Added `setText(value)` to the imperative handle: it replaces the textarea
value, reports the empty↔non-empty flip, then focuses the textarea with the
caret at the end. Used by [[OnboardingJourney]]'s suggested-prompt chips —
clicking a chip fills the composer (it does NOT auto-send; the user reviews
then hits Enter). Needed a real ref to the underlying `<textarea>`, so the
component now holds `textareaRef` and forwards it to `Textarea` (which already
forwards refs to the element).

# Composer.tsx — isolated chat message textarea

## Why it exists

Split out of `ChatPanel.tsx` (2026-05-22) to fix chat-input typing lag. The
draft text was `input` state inside ChatPanel, which subscribes to the entire
chat store and renders the whole message timeline — so every keystroke
re-rendered that monolith, and typing during streaming (one-char-per-token
models like DeepSeek via aggregators) collided two re-render storms. Holding
the text in this small memoized child means a keystroke re-renders only here.

## Contract with ChatPanel

- **Imperative handle** (`ComposerHandle`): `getText()` (read on send) and
  `clear()` (after a successful send). ChatPanel never holds the text as state.
- **`onEmptyChange(isEmpty)`** fires ONLY on the empty↔non-empty flip (not per
  keystroke) so the Send button's disabled state stays correct without
  re-rendering ChatPanel per character.
- **`onSubmit`** fires on Enter (no Shift, not mid-IME). ChatPanel passes a
  STABLE wrapper (ref-backed `useCallback`) so this memoized component does not
  re-render when ChatPanel re-renders (e.g. streaming). Same for the drag/paste
  handlers — they're also bound to ChatPanel's wrapper div, so they live in
  ChatPanel and are handed down as stable wrappers.

## Design decisions / gotchas

- **Draft persistence is debounced** (400ms) and flushed on unmount; ChatPanel
  remounts via `key={agentId}` so each agent's draft restores from
  `chatDrafts` on mount. Don't move draft state back up — that reintroduces the
  per-keystroke localStorage write and the lag.
- **IME composition guard**: Enter within 100ms of `compositionend`, or while
  `isComposing`, does not submit (CJK input). The refs live here now.
- The textarea must keep the drag/paste handlers (native default would insert a
  dropped file path / paste-as-text otherwise) — see ChatPanel's wrapper-div
  comment.
- 铁律 #16: this is pure render isolation — no message content is dropped,
  truncated, or throttled.

## 2026-09-04 · UI slot points (batch 3d.2)

A `SlotOutlet` strip above the textarea mounts `composerExtensions` (when-filtered, `agentId` passed).
