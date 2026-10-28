---
code_file: frontend/src/components/settings/PersonalizationSettings.tsx
last_verified: 2026-09-08
stub: false
---

## 2026-09-08（本地 E2E 实测）— 插件主题选择器

`ui.themes` 注册表此前无任何 UI 消费方（`applyTheme` 零调用）：插件主题注册成功却选不到。现在在基础主题下方按需（有条目才出现）
列出插件主题 + 「默认外观」，选择写 `themeStore.pluginTheme`；主题声明 `dark` 时同步切基础主题。应用逻辑在 `hooks/usePluginTheme.ts`。

## 2026-08-30 — 第三个控件：进度短讯（独白提级）开关

theme / language 之外多一个 switch，写 [[uiStore]] 的 `interimNarration`
（localStorage，缺省开）。**放这里的理由就是本文件头那句**「everything
configurable belongs to this page」——不要再在 sidebar 账号气泡里开第二个
设置面。

它是**纯前端显示偏好**，不进后端、不进 env：它治理的是「读者觉不觉得吵」，
不是平台风险（对照 `NARRATIVE_MERGED_ROUTING_ENABLED` 那类发布 flag 的判据，
见 [[uiStore]]）。

10 个语言包一次补齐——本单已经因为 `chat.timeline.narration` 建立了这个先例，
别只补 en/zh。

**文案键放 `pages.settings.personalization.*`**，和这个 pane 已有的
`label` / `hint` 同一个对象（Privacy pane 的正文文案也放在自己 section 里，
同一先例）。第一版放在了 `settings.personalization.*`，于是两个都叫
`personalization` 的对象各装着这个面板的一半文案——改文案的人要在两处找、
×10 个语种。review 第 4 轮迁并。新增 Personalization 文案一律进
`pages.settings.personalization`。

# PersonalizationSettings — 设置页「个性化」pane

主题(system/light/dark 三档 radio,走 [[useTheme]].setTheme)+ 语言
(SUPPORTED_LANGUAGES 列表,i18n.changeLanguage)。这两个控件**只**活在
这里——它们从侧栏账户弹层迁入,弹层从此只做身份(见 [[../layout/Sidebar]]
08-19 条)。标签复用 `sidebar.theme*`/`sidebar.language` 既有 key;栏目自身
的 key 是 `pages.settings.nav.personalization` / `pages.settings.personalization.*`。
将来新增个性化项(密度、字号…)加在此 pane,别再往弹层塞。
