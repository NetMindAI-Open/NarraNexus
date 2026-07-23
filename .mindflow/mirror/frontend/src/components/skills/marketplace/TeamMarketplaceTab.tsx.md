---
code_file: frontend/src/components/skills/marketplace/TeamMarketplaceTab.tsx
last_verified: 2026-07-21
stub: false
---

# TeamMarketplaceTab.tsx

Team Marketplace tab: lists team/agent bundle templates as cards (name,
agent-count badge, category chips, description) with derived category
filtering + client search. Install does NOT duplicate any import logic — it
routes to /app/templates/install?teamTemplate=<id>, and BundleImportPage's
new team-template deep-link runs the server-side install-preflight then the
existing review/confirm wizard. Fetches GET /api/marketplace/teams/templates;
the error state doubles as the desktop-offline UX.
