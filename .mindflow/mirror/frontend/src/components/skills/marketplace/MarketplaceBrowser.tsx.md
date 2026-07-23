---
code_file: frontend/src/components/skills/marketplace/MarketplaceBrowser.tsx
last_verified: 2026-07-21
stub: false
---

## 2026-07-21 — MarketplaceCard 导出

`MarketplaceCard` 改为具名导出,MarketplacePage(全页入口)复用同一张卡片。


# MarketplaceBrowser.tsx

Skill Marketplace browse/search dialog, opened from SkillsPanel's action-bar
"Marketplace" button. 300ms debounce between the input state and the query
key (so typing doesn't spam the API); cards show scan badge, downloads,
installed / update-available flags (injected server-side when agent_id is
sent); Install goes through useMarketplaceInstall (which invalidates BOTH
the installed-skills list and marketplace queries so flags refresh); card
click opens SkillDetailSheet. The error state doubles as the desktop-offline
UX — the cloud registry being unreachable shows "unavailable", never breaks
the panel (spec: 离线降级).
