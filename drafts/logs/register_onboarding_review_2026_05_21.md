# New-user register → onboarding flow review (cloud version)

- **Trigger**: Hongyi is reviewing the end-to-end "register → first use" chain for a brand-new cloud user, wants to align on gaps before fixing.
- **Branch / commit**: NarraNexus `main` @ `9a19b3a` (v1.7.1, invite+templates merged via `2519fa0`). narranexus-website `master` @ `77b13e1` (invite+templates merged via `bdcbcbe`).
- **Status**: review done, waiting-for-user — design decisions open on points 3 & 4.
- **Scope**: cloud version only (`agent.narra.nexus` + `website.narra.nexus`).

---

## The flow, as it actually runs today (verified against code)

### Step 1 — website.narra.nexus → "Try Online"
- Homepage hero button "Try Online" → `https://agent.narra.nexus` directly (`narranexus-website/app/page.tsx:217-224`). Same target on the Quick Start "Cloud" card (`page.tsx:535`).
- **No mention that cloud is invite-only.** The header nav DOES have "Request Access" → `/invite` (`components/header.tsx:13`), but the primary CTA walks the user straight into the register wall.

### Step 2 — agent.narra.nexus → create account
- `RegisterPage.tsx` — cloud-only, requires invite code. Has fields: username / password / confirm / **Invite Code** (placeholder "enter your invite code", `RegisterPage.tsx:190-200`).
- Subtitle: "Create account · Invite required" (`:143`).
- **Gap: zero guidance on where to get a code.** No notice, no link to `website.narra.nexus/invite`. Only escape hatches are "Back to Sign In" and "Change mode". A user without a code is simply stuck.

### Step 3 — get code from email → register → land on Settings
- `api.register()` succeeds → `login()` → `navigate('/')` → `RootRedirect` (`App.tsx:138-200`).
- `RootRedirect` calls `api.getProviders()`; if provider count === 0 → `Navigate to /setup` (`App.tsx:196-197`).
- `SetupPage.tsx` renders `<ProviderSettings/>` with header "Configure LLM Providers" + footer "Skip for now" / "Done".
- **NetMind power discoverability**: `PRESET_PROVIDERS` lists NetMind.AI Power first, with `get_key_url: https://www.netmind.ai/user/dashboard` (`ProviderSettings.tsx:122`). So there IS a get-key link. But NetMind is presented as one of three equal presets (NetMind / Yunwu / OpenRouter) — **not framed as "the recommended easy path for a beginner"**.
- **Model auto-default — ALREADY SOLVED**: when NetMind is Quick-Added, `PRESET_DEFAULT_SLOTS.netmind` auto-fills all three slots (`ProviderSettings.tsx:140-149`):
  - agent → `deepseek-ai/DeepSeek-V4-Pro`
  - helper_llm → `deepseek-ai/DeepSeek-V4-Flash`
  - embedding → `BAAI/bge-m3`
  - It only fills EMPTY slots (upsert, never clobbers). So after pasting one NetMind key the user can hit "Done" directly — no manual model wiring. This part of point 3 needs **no change**.

### Step 4 — first use: create an agent / use a template
- After Setup → `/app/chat`. New account has 0 agents (RegisterPage comment `:72` "empty agents is fine").
- `AgentList.tsx:442-458` empty state: "No agents yet / Create your first agent to start a conversation." + "Create Agent" button.
- `ChatPanel.tsx:857-866` empty state: "Select an agent / Choose an agent from the sidebar."
- Creating an agent → fresh agent greets with `BOOTSTRAP_GREETING` ("Hi there... I just woke up...", `ChatPanel.tsx:184`) asking for its name + the user's name. Nice first-touch once you get there.
- **Gaps**:
  - **No in-app pointer to the templates marketplace at all.** The only template-ish entry is `TeamFilterBar.tsx:74` "Import a .nxbundle" → `/app/bundle/import` — that's a manual file-upload importer, not a link to `website.narra.nexus/templates`.
  - **No first-login tutorial / walkthrough / onboarding modal exists** (grep for onboard/tutorial/walkthrough/welcome → nothing in cloud UI).
  - Empty states are functional but minimal — they don't tell the user "you can also start from a template instead of from scratch".

---

## Gap summary vs Hongyi's 4 modify-points

| # | Point | Verdict | Where |
|---|---|---|---|
| 1 | Register page needs notice + link back to invite page | **Confirmed missing** | `RegisterPage.tsx` — add a line under the Invite Code field |
| 2 | Spam-folder notice on invite page | **Confirmed missing** | `narranexus-website/app/invite/page.tsx` — success state has no spam hint |
| 3a | First user discoverability of NetMind power | **Partially there** — listed first + get-key link, but not framed as the recommended beginner path | `ProviderSettings.tsx:122` |
| 3b | Auto-select model so user can directly "Done" | **Already done** — `PRESET_DEFAULT_SLOTS.netmind` fills all 3 slots on Quick-Add | `ProviderSettings.tsx:140-149` |
| 4 | New user guided to create an agent / use templates | **Confirmed missing** — no templates pointer in-app, no onboarding | `AgentList.tsx`, `ChatPanel.tsx`, no onboarding component exists |

---

## Open design decisions (need Hongyi's call)

1. **Step 1 entry** — should "Try Online" on the website go straight to `agent.narra.nexus`, or to `/invite` first? Or keep direct but add an "invite-only" hint near the button?
2. **Point 4 onboarding shape** — three candidate approaches:
   - (a) Enrich the empty states: two explicit paths "Create from scratch" vs "Start from a template" (the template path links out to `website.narra.nexus/templates`).
   - (b) A dismissible first-login notice card.
   - (c) An opt-in short walkthrough the user can choose to take or skip.
3. **Template install loop** — templates marketplace lives on the website; the in-app side only has `/app/bundle/import`. Should the in-app empty state deep-link to the website `/templates`, or should there be an in-app template browser? (Bigger scope — relates to Phase 3.)

## Next step
- Hongyi to decide on the 3 open questions above.
- Points 1, 2, 3a are small, well-scoped UI edits — can be done immediately once direction confirmed.
- Point 4 depends on decision #2.

---

## 更新 2026-05-21 — implemented

Branch `user_loop_2026_05_21` on both repos. Decisions taken:
- **Step 1**: keep "Try Online" direct-jump + add an invite hint line.
- **Point 4 onboarding shape**: dismissible re-entrant checklist card.
- **Template entry**: deep-link out to `website.narra.nexus/templates`.

### What shipped

**website (`user_loop_2026_05_21`)**
- `app/page.tsx` — invite-only hint + `/invite` link under the hero CTAs.
- `app/invite/page.tsx` — spam-folder notice on the issued-code success state.

**NarraNexus (`user_loop_2026_05_21`)**
- Backend: `OnboardingProgress` / `OnboardingResponse` / `UpdateOnboardingRequest`
  schemas; `GET` + `POST /api/auth/onboarding` (state in `users.metadata`,
  write-once-true flags, merge-safe). 6 tests in `tests/backend/test_onboarding.py`.
- `frontend/src/hooks/useCreateAgent.ts` — shared agent-create action
  (also fires the `first_agent_created` onboarding mark). `AgentList`
  refactored onto it.
- `frontend/src/components/onboarding/OnboardingChecklist.tsx` — the card,
  injected at the top of the chat column in `MainLayout`. 3 rows:
  configure provider (derived) / create first agent / start from template.
- `BundleImportPage` marks `template_applied` on confirmed import.
- `RegisterPage` — "No invite code? Request one here" link + spam hint.
- `ProviderSettings` — NetMind.AI Power framed as the recommended start.

### Verification
- Backend: 28 tests green (onboarding 6 + invite + bundle-from-url).
- Frontend: tsc clean, vite build OK, 201 vitest pass (2 pre-existing
  mock/localStorage test-file failures, unrelated).
- Lint: 80 pre-existing errors, **0 added**.

### Status
done — pending Hongyi review on the branches, then merge.
