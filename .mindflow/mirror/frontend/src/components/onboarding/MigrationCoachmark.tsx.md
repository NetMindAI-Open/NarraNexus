---
code_file: frontend/src/components/onboarding/MigrationCoachmark.tsx
last_verified: 2026-07-30
stub: false
---

# onboarding/MigrationCoachmark.tsx — bubble pointing at the sidebar "+"

## Why it exists

The second half of [[MigrationGuide]]: after the user dismisses the welcome modal
via Later/X, point them at where import lives — the sidebar "+" (Create menu).

## Design decisions

- **Anchors to `data-help-id="sidebar.create-agent"`** (the same anchor the
  [[HelpOverlay]] uses): `querySelector` + `getBoundingClientRect`, positioned to
  the RIGHT of the "+". Re-measures on resize + a short interval (the sidebar can
  mount/relayout after this component).
- **Portals to `document.body`** so the sidebar's scroll/overflow never clips it.
- **Expanded sidebar only for now** (Owner: "先做展开态"): if the anchor is
  missing or zero-size (collapsed rail), it renders nothing.
- Stays until the user clicks "Got it" (`onDismiss` → `coachmarkDone`) — no
  auto-fade ("挂到点掉").

## 2026-08-19 — 渲染下沉到 AnchoredCoachmark

GuideAgentCoachmark 曾是本文件的逐字复制，且本地首跑两者可同时武装、在同
一 fixed 坐标像素重叠。测量/portal/500ms 抢挂载/10s 放弃全部下沉到共享的
`AnchoredCoachmark`（带同锚排队：同一时刻一个锚点只渲染一个气泡，持有者
消失后等待者接管）。本文件只剩门控（仍由 MigrationGuide 持有）与文案；
i18n key 未动。
