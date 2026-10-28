---
code_file: frontend/src/components/icons/ChannelBrandIcons.tsx
last_verified: 2026-08-27
stub: false
---

# ChannelBrandIcons.tsx — IM 渠道的真实品牌标志

## 为什么存在

Dashboard 的智能体目录要回答「这个 agent 接了哪些渠道」。用 lucide 通用图标
（Discord→Bot、Slack→Hash 这种占位符）表达不了品牌身份，一列灰色小方块用户根本
认不出来。项目里没有任何现成的品牌 logo 资产或图标库（`package.json` 只有
`lucide-react`），所以这里从 Simple Icons（CC0，`cdn.jsdelivr.net/npm/
simple-icons@latest/icons/<slug>.svg`）取官方单色路径，本地建组件，不引入新依赖。

## 上下游关系

- **调用方**：[[../../pages/DashboardPage.tsx]] 的 Channels 列（`CHANNEL_BRANDS`
  映射表 + tooltip）、[[../../pages/AgentProfilePage.tsx]]。
- **数据来源**：`AgentInfo.bound_channels`，由 `/api/auth/agents` 一次 UNION 查询
  投影出来（见 [[../../../../backend/routes/auth.py]]）。渠道名和这里的导出组件
  必须一一对应；后端加渠道而这里不加，调用方会退化成通用 Bot 图标。

## 覆盖范围与两个例外

七个渠道全有真实标志。Discord / WeChat / Slack / Telegram / Home Assistant 是
Simple Icons 的官方 monochrome 矢量路径，填各自品牌 hex（不是 `currentColor`）——
Owner 要的是能认出来的彩色 logo，不是跟随墨色的灰剪影。

- **Lark/Feishu 没有矢量可用**。Simple Icons 目录里没有 `lark` / `feishu` /
  `larksuite` 条目，索引里只有母公司 "ByteDance"，是另一个标志；再用 Iconify 全集
  搜索确认了一遍，搜出来的都是「云雀鸟」图标或不相关的 olark 客服产品。所以改用
  larksuite.com 自己的 favicon（官方资产，权威来源，不是第三方描摹），存成
  `public/channel-logos/lark.png`。`LarkBrandIcon` 因此是本文件唯一一个 `<img>`
  而不是 `<svg><path>`，按根相对路径引用（和 `Sidebar.tsx` 引 `/logo-*.svg` 一致），
  不走 JS import 打包。
- **OpenAI 和 Slack 的色号不来自 Simple Icons 数据**。这两个品牌当前已不在 Simple
  Icons 的 metadata 注册表里（`data/simple-icons.json` 搜不到，估计是品牌方要求
  下架；jsdelivr CDN 还在返回旧文件属于缓存滞后，不代表官方仍认可这个用法）。
  Slack 用官方 brand style guide 公开的 Aubergine `#4A154B`，OpenAI 用黑
  （其标志本就没有强调色）。

## 设计决策

- `NarraMessengerBrandIcon` 和 `NexusPowerBrandIcon` 都是本产品自己的第一方表面，
  没有独立设计的品牌资产，「真实图标」就是本仓库已有的 logo。两者共用私有的
  `AppLogoIcon({className, alt})`，内部用 `useTheme()` 的 `isDark` 切
  `/logo-dark-mode.svg` / `/logo-light-mode.svg`——**这是本文件唯一会用 hook 的
  路径**，其余都是纯函数组件。两个真实场景共用一份渲染逻辑，不是提前抽象。
- 矢量组件签名是 `SVGProps<SVGSVGElement>`、`viewBox="0 0 24 24"`（和 lucide 网格
  一致），调用方 `<DiscordBrandIcon className="h-4 w-4" />` 的用法与 lucide 图标
  完全可互换。
- Path 数据从 CDN 原样取用，没做任何简化/近似——品牌标志画错比不放更糟。

## Gotcha

**`<img>` 组件和 `<svg>` 组件的 props 不是一回事**：`LarkBrandIcon` /
`NarraMessengerBrandIcon` / `NexusPowerBrandIcon` 只接 `{ className }`，不接
其余 SVG props，也不跟随 `currentColor` 或 `fill`。调用方如果要做灰阶/变暗之类的
状态切换，必须用 opacity/grayscale 滤镜这种对位图和矢量都生效的手段，不要写
`text-[var(--nm-ink30)]` 那套只对 `currentColor` 有效的方案——历史上就是靠散落的
`key === 'lark'` 特例判断，加第二个 `<img>` 图标时漏掉，导致图标「常亮」。
