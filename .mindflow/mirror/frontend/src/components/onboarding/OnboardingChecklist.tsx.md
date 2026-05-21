---
code_file: frontend/src/components/onboarding/OnboardingChecklist.tsx
last_verified: 2026-05-21
stub: false
---

# OnboardingChecklist.tsx — new-user "getting started" card

## Why it exists

A brand-new cloud user lands on an empty chat with no guidance on what to
do next — the register → first-use chain had zero onboarding (see
`drafts/logs/register_onboarding_review_2026_05_21.md`). This card sits at
the top of the chat surface and gives them the three concrete next steps.

## Design decisions

**Re-entrant, not a wizard.** The two starting paths — "create your first
agent" and "start from a template" — are independent checklist rows, not
an either/or fork. Completing one leaves the other actionable. The card
hides only when BOTH paths are done, or on explicit dismiss. This was a
deliberate call: a one-shot walkthrough would trap a user who picked the
"wrong" path first.

**State split — stored vs. derived.** `first_agent_created`,
`template_applied`, `dismissed` are write-once-true flags persisted in
`users.metadata.onboarding_progress` (backend `GET/POST /api/auth/onboarding`).
`provider_configured` is NOT stored — it is derived live from provider
count, because that step is gated by SetupPage before this card ever
shows. Row 2 additionally OR-s in live `agents.length > 0` so creating an
agent from the sidebar reflects instantly (the persisted flag still
latches it, so deleting the agent later doesn't un-check the row).

**Cloud-only.** Renders null outside cloud mode. The progress *flags* are
written mode-agnostically by `useCreateAgent` / `BundleImportPage` — only
this card is gated. If onboarding is later wanted in local mode, the data
is already there.

## Upstream / downstream

- Rendered by `MainLayout.tsx` (`ChatView`), above `ChatPanel`.
- Reads: `api.getOnboarding`, `api.getProviders`; `configStore` (userId,
  agents); `runtimeStore` (mode).
- Writes: `api.markOnboardingStep` (dismiss); delegates agent creation to
  the `useCreateAgent` hook (which itself marks `first_agent_created`).
- "Browse templates" opens `website.narra.nexus/templates` in a new tab —
  the marketplace lives on the marketing site, not in-app.

## Gotchas

Opening the templates page is NOT completion — `template_applied` flips
only when a bundle import actually confirms (`BundleImportPage.runConfirm`).
So the row stays unchecked until the user finishes an install.
