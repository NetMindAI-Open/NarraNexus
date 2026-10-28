---
code_file: frontend/src/components/bookmarks/__tests__/drawerPanelSwitcher.test.tsx
last_verified: 2026-09-11
stub: false
---

# drawerPanelSwitcher.test.tsx — the drawer title must stay a panel switcher

## 为什么存在

Owner 硬性要求(2026-09-11,推翻 #383 的退役):抽屉标题是面板切换器,钉住的抽屉是独立窗口,
能自己换内容。本测试钉住 [[../BookmarkDrawer]] 的切换器行为,删掉切换器会一片红:

- 标题是 `aria-haspopup="menu"` 按钮,点开列出 `allTabs()` 全部面板(按注册顺序),按
  `stripCategories()` 分组;
- 触发按钮的可访问名以面板标题开头(`ARTIFACTS · Switch panel`),标题不能被 aria-label 抹掉;
- 当前面板 `aria-checked="true"` 且只有它;
- 选别的面板回调 `onSelectTab(id)` 并关菜单;选当前面板只关菜单;
- 外部 pointerdown 关菜单,菜单内部不关;
- 无 studio 时 `visibleCategories` 不提供 `builder`(与 ⋯ / ⌘K 同一规则);
- 不传切换器 props 时标题是纯文本;
- Escape:未钉住(`pinned={false}`)时菜单开着按 Esc 只关菜单、`onClose` 不被调用,再按一次才关抽屉;
  钉住时 Esc 只关菜单、`onClose` 永不调用;抽屉关掉再打开不会复活菜单;
- 团队注册表 `teamDrawerCategories` 恰为 members/artifacts/files/manage,计数 >0 才显示。
