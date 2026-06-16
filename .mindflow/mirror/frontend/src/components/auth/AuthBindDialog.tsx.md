---
code_file: frontend/src/components/auth/AuthBindDialog.tsx
last_verified: 2026-06-11
stub: false
---

# AuthBindDialog.tsx — OAuth first-time account binding dialog

## Why it exists

When a user signs in via a third-party OAuth provider (NetMind) for the
first time, the backend may determine that the account is not yet fully
bound. The `userCallBack` endpoint returns a `bandType` field (1, 2, or 3)
signalling what additional confirmation is needed before the login can
complete. This component renders the appropriate dialog for each case.

It is a separate file (not inlined into the login page or auth hook)
because the three binding scenarios each have distinct UI: one renders
an input form, the others are pure confirmation prompts. Keeping this in
a single dedicated component keeps the parent hook (`useNetMindAuth` or
equivalent) free of JSX.

## The three binding scenarios

| bandType | Meaning | UI |
|---|---|---|
| 1 | New user — no existing account found. Need email + verification code to create/bind one. | TextInput for email + TextInput for verifyCode + "Send code" action |
| 2 | Third-party account already has an email; confirm using that email to complete binding. | Read-only email display, confirm button only |
| 3 | Existing NarraNexus account found; confirm binding to it. | Confirm button only |

`bandType` values 2 and 3 carry no user-entered data — `onSubmit` is
called with an empty object `{}`. Only `bandType === 1` collects
`{ email, verifyCode }`.

## What this file does NOT do

- Does not issue HTTP requests directly. Data fetching (send-code,
  submit-bind) is delegated to the `onSendCode` and `onSubmit` callbacks
  passed by the parent (typically `useNetMindAuth` hook).
- Does not manage the binding state machine. It renders according to
  the `bandType` the parent resolved; transitions belong to the hook.
- Does not handle the OAuth redirect itself — that lives in the auth
  callback route.

## Upstream / Downstream

- **Upstream (callers)**: The NetMind auth hook / login page mounts this
  dialog when `userCallBack` returns `bandType` 1, 2, or 3. The parent
  passes `bandType`, `callbackEmail` (for bandType 2), `onSendCode`,
  `onSubmit`, and `onClose`.
- **Downstream (deps)**: NM design-system primitives (`FormField`,
  `TextInput`, `Button`, `Modal`) from `nm/form.tsx`, `nm/modal.tsx`,
  `nm/button.tsx`.

## Design decisions

**Single component for all three bandTypes** rather than three separate
dialogs. The three cases share the modal chrome, title/subtitle structure,
and a single primary action button. Conditional rendering of the input
block (only for bandType 1) is minimal enough to not warrant a split.
If a fourth binding scenario is added with substantially more UI, extract
at that point.

**onSubmit shape: `{}` for bandType 2/3`**. The parent hook already knows
the bandType, so it doesn't need the component to re-send it. An empty
object signals "user confirmed" cleanly without coupling the dialog to
the binding payload structure.

**Send-code lives on the parent, not inside the component.** This keeps
the component a pure presentation unit and avoids duplicating rate-limit
or error-state logic that belongs in the hook.

## Gotcha / edge cases

- **Trigger**: If `bandType` is anything other than 1, 2, or 3 (e.g., the
  backend adds a new type) → **Symptom**: the dialog renders with no
  body content and a disabled submit button → **Root cause**: the
  conditional rendering only covers the three known values. Add a new
  branch when the backend introduces a new `bandType`.
- **Trigger**: When `bandType === 2` and `callbackEmail` is undefined →
  **Symptom**: the email display slot renders empty/blank → **Root cause**:
  caller did not forward `callbackEmail` from the `userCallBack` response.
  Check that the parent extracts and passes this prop.

## Related constraints

- Iron rule #3 (module independence) — this component does not import from
  other feature modules (jobs, chat, etc.), only from nm/ primitives.
- See `references/auth_netmind_migration.md` (Phase 1 spec) for the full
  binding flow and payload shapes.
