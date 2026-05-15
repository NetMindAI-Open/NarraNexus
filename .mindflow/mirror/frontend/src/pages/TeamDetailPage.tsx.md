---
code_file: frontend/src/pages/TeamDetailPage.tsx
last_verified: 2026-05-08
stub: false
---

# TeamDetailPage.tsx — Team detail view (议题 8 onboarding)

Renders `teams.intro_md` (markdown) + member roster + "Edit team" shortcut.

Reached by:
- Sidebar `TeamFilterBar` chip → external-link icon (when team is the active filter)
- Bundle import "Done" page → "View team intro" button (when bundle had a team)
- Direct URL `/app/teams/:teamId`

## Why intro_md is here

议题 8 decided onboarding = README.md travels with the bundle, populates
`teams.intro_md` on import. Recipient should see this content prominently
when first exploring the new team — this page is that surface.
