---
code_file: frontend/src/pages/MarketplacePage.tsx
last_verified: 2026-07-21
stub: false
---

# MarketplacePage.tsx

Marketplace shell — one left-sidebar entry (/app/marketplace), two tabs:
Skills (extend one agent) and Teams (fork a whole team/agent bundle). The
active tab is reflected in ?tab= so links/refreshes are stable. Tab bodies
are SkillMarketplaceTab (the former page content, moved into a component)
and TeamMarketplaceTab. This is decision 2 (T1) from the team-marketplace
design: one entry, two object tabs.
