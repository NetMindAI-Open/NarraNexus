---
code_file: frontend/src/components/awareness/EntityCard.tsx
last_verified: 2026-04-10
stub: false
---

# EntityCard.tsx — Expandable contact card in the social network list

Shows a compact header (name, type, chat count, relationship strength badge) that expands to reveal: persona/communication style, related job IDs, expertise domains, description (Markdown), tags, identity info, and contact info.

The current user's card (`isCurrentUser`) starts expanded by default and gets accent styling. All other cards start collapsed.

`relationship_strength >= 0.7` = "Strong" (green), `0.4–0.7` = "Medium" (yellow), `< 0.4` = no badge. Used in both the default list and search results.
