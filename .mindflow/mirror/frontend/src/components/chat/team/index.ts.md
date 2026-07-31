---
code_file: frontend/src/components/chat/team/index.ts
last_verified: 2026-07-31
stub: false
---

## 2026-07-31 — barrel 增补 TeamMemberPanel（roster v2 的终端卡）

# team/index.ts — barrel for the team group-chat surface

[[MainLayout]] mounts `TeamChatPanel` and nothing else; [[TeamRosterPanel]] and
[[TeamRoomHero]] (which also lends out `GuideRuleCards`) are its parts and are
exported for tests, not for other screens to compose with. The package exists
because the surface reached three files (铁律 #23) — before the split they were
`Team*`-prefixed files sitting flat in `components/chat/`.

The barrel is where a retired part is noticed: `TeamActivityConsole` (folded
status console, 2026-07-30) and `TeamRoomGuide` (the addressing banner the hero
and the `?` popover replaced, 2026-07-30) were dropped from it when their files
were deleted.
