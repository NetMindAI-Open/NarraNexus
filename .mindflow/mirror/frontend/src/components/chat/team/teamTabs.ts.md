---
code_file: frontend/src/components/chat/team/teamTabs.ts
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — `teamDrawerCategories` / `TeamTabCounts` 回归,含 manage

抽屉标题切换器按 Owner 要求恢复([[../../bookmarks/BookmarkDrawer]] 同日条),本文件重新提供
切换器注册表:`teamDrawerCategories({members, artifacts, files})` 返回一个分组(i18n
`chat.team.drawerCategory` ×10),四项 members(Users2,计数)/ artifacts(ArtifactsGlyph,计数)/
files(FolderOpen,计数)/ manage(Settings2,无计数,排最后)。label 仍唯一来源
`TAB_LABEL_KEYS`。下方 09-03「只剩 id + labelKey」条已作废。
`ArtifactsGlyph` 与 `DrawerSwitcherCategory` 类型都从 barrel `@/components/bookmarks` 导入。

## 2026-09-03 — 第四个 tab:manage

`TeamTabId` 加 `'manage'`(Settings2,labelKey `chat.team.manage.title`,无计数),排最后。
面板见 [[TeamManagePanel.tsx]]。

## 2026-09-03 — 只剩 id + labelKey(切换器退役的连带)

抽屉标题下拉删除后([[../../bookmarks/BookmarkDrawer]]),`teamDrawerCategories`
与 `TeamTabCounts` 没有消费方,连 lucide/ArtifactsGlyph 图标依赖一起删掉。
本文件回到最小形态:`TeamTabId` + `TAB_LABEL_KEYS` + `teamTabLabelKey`。
计数不是丢了而是**搬到入口上**——members/artifacts/files 三枚 member bar
toggle 各自带计数(见 [[TeamChatPanel.tsx]]),仍然满足「入口必须宣传自己
的内容」。`Record<TeamTabId,…>` 的穷尽性保留:加面板漏 label 直接编译错。

## 2026-08-19(二)— 注册表变 builder(带活计数)

`teamDrawerCategories({members,artifacts,files})` 返回带 count 的注册表
(共享文件的数量此前无处可见);label key 单一来源 `TAB_LABEL_KEYS`
(builder 与 `teamTabLabelKey` 同吃,改名只动一处;Record 的穷尽性让
加新 tab 时漏映射直接编译错)。

# teamTabs — 团队房间的抽屉面板注册表

members(roster)/artifacts/files 三项,喂给 [[../../bookmarks/BookmarkDrawer]]
的 switcherCategories。「team 右侧=单聊右侧同一逻辑」的差异只剩这份清单。
labelKey 复用既有 roster.title/workspace.tab*;分组名 chat.team.drawerCategory
(×10)。加面板=在这里加一行。
