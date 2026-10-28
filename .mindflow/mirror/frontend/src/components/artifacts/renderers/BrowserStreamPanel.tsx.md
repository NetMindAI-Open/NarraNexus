---
code_file: frontend/src/components/artifacts/renderers/BrowserStreamPanel.tsx
last_verified: 2026-09-24
stub: false
---

2026-09-24：标签组件接入新建/导航 pending 状态；等待命令回执时禁用交还，保证
一次人工操作完整完成后再恢复 Agent。地址栏在 canvas 输入区域之外，不把本地编辑
按键转发到远程网页。

## 2026-09-23 多页面支持

BrowserPageTabs 显示标题、网址、Agent 当前页与跟随按钮。画布通过 stream callback
ref 挂载，首次连接再快也能调整远程视口。接管、键盘及 pointer 继续由 hook 与服务端
验证具体页面，不会因用户旁观另一标签而改变 Agent 当前操作。

## 2026-09-23 - Typed installation errors

Install/cancel failures accept a server error only when it is a string;
otherwise they use the localized installation error. This narrows the shared
result union without changing the panel's installation or input ownership.

# BrowserStreamPanel.tsx — 实时浏览器面板

选择本机 Chrome 后如运行时不可用，展示正式 Chrome 的恢复指引和重新检查按钮，
不能显示托管浏览器下载按钮并误导用户。来源选择不改变画布、接管或默认内嵌运行方式。

订阅实际会话不依赖新会话运行时的就绪状态。已有 live 流优先于安装卡，避免来源
切换后新运行时不可用或状态查询故障遮住正在使用的旧浏览器。

## 为什么存在 / 关键决定

独立的 Browser 活动面板展示 Agent 的 Chromium 画面；它不依赖 URL artifact，
也不替换 UrlRenderer。运行时默认 headless，用户通过应用内画布观看和接管。

**运行时不可用时显示安装卡，绝不是空白画布。** 运行时是用户一次性手动安装的（设计 §8）；
什么都不渲染会让用户完全无从判断发生了什么。

**「没装」和「装了但起不来」说不同的话**（install vs re-install）。合并成一句，
就把用户唯一的线索扔了。

**总量未知时不编百分比。** 有些 CDN 不给 Content-Length；用未知总量凑一个百分比是在骗用户，
`null` 让 UI 渲染不确定态。

**看是免费的，驱动是排他的。** agent 工作时帧照常推送；输入只在显式接管之后才转发，
所以一次无意的鼠标移动偷不走指针。

2026-09-22 补全：只有服务端确认本连接 `can_control` 后才发送输入；另一个窗口
接管时本窗口保持旁观。`useBrowserStream` 管连接、鉴权、重连和异步帧生命周期；
`browserInput` 负责 object-contain 留白下的坐标换算。原生非 passive wheel 防止
滚动到外层应用。隐藏 textarea 接收中文组合输入、移动键盘和粘贴，发送 text 事件。
画布尺寸由容器固定，不让收到的图像原始尺寸挤压工具栏。
鼠标、拖动、滚轮和触摸共用绘制帧对应的页面尺寸，避免位图被 Chromium 缩放后点击偏移。

运行时串行轮询能看到从设置页发起的安装，安装支持取消；状态查询失败显示重试，
不能冒充“尚未安装”。未启动会话、正在连接、断线重连分别显示状态。

## 上下游

- 设计：`reference/self_notebook/specs/2026-09-21-in-app-browser-design.md`
