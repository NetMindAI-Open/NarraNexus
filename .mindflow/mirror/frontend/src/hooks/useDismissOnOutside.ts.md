---
code_file: frontend/src/hooks/useDismissOnOutside.ts
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — 外层可通过「持有弹层开关状态」让出 Escape(仅注释)

hook 行为不变。docstring 补一句:外层若自己持有弹层的 open 状态、并在弹层打开时跳过自己的
Escape,就不会一起关——[[../components/bookmarks/BookmarkDrawer]] 对其标题切换器就是这么做的
(只此一处;面板内容里的其它弹层仍会连抽屉一起关,通用的「最顶层优先」仍需全局 popover 栈)。

## 2026-08-19 — extraRefs、Escape 语义、iframe blur、DEV 警告

- 新第三参 `extraRefs`:portal 形态(trigger 与面板不同父)把两个 ref 都
  交进来,任一 `contains(target)` 即视为内部。[[../components/artifacts/ArtifactDownloadMenu]]
  借此收编,仓内不再有第二套手写「点外面关闭」。
- **Escape 关闭,作用域如实**:监听在 document 冒泡阶段,`stopPropagation`
  只能挡住 window 级 Escape(nm/modal 家族);Dialog/SettingsModal/
  ZoomModal/BookmarkDrawer 这些同在 document 的处理器仍会触发——叠在它们
  里面的弹层会与宿主同一击关闭。要做全应用「最顶层优先」需要共享弹层栈,
  不是本 hook 的一个 flag。
- iframe 盲区:跨源 iframe 内的点击到不了 document,补 `window blur` +
  `activeElement 为 IFRAME` 才 dismiss(切标签/切应用的 blur 不带 IFRAME
  焦点,弹层正确保留)。
- DEV 下 active 而无任何 ref 挂上时 console.warn——忘挂 ref 的表现恰是
  本 hook 要修的那个 bug,必须吵。

# useDismissOnOutside — 弹层的「点外面/Escape 关闭」唯一实现

document 级监听替代全屏 backdrop `<div>`(pointerdown 走 capture;
keydown/blur 为普通阶段)。
backdrop 方案的坑:`position: fixed` 相对最近的 **transform 祖先**布局,
弹层长在带动画的行里(`animate-slide-up` fill: forwards 保留 transform)时,
"全屏"遮罩只盖住那一行——这正是「点页面别处关不掉弹窗」的根因。

约定:返回的 ref 挂在**同时包含触发器和面板**的容器上;容器内交互不 dismiss。
回调经 latest-ref 转发,调用方可放心传内联闭包(不会引起监听重订阅)。
capture 阶段监听,中途的 stopPropagation 拦不住它。

消费方:[[../components/layout/AgentRowMenu]] / [[../components/layout/TeamRowMenu]] /
[[../components/layout/CreateMenu]] / [[../components/layout/Sidebar]](账户弹层) /
[[../components/chat/ChatHeader]](⋯ 菜单与 Agent 切换器) /
[[../components/bookmarks/BookmarkDrawer]](面板切换器) /
[[../components/artifacts/ArtifactDownloadMenu]](extraRefs portal 形态)。
新弹层一律用它,别再手写 backdrop。
