---
code_file: frontend/src/pages/CreateUserDialog.tsx
last_verified: 2026-04-10
stub: false
---

# CreateUserDialog.tsx — Local-mode user creation modal

## Why it exists

In local mode, there is no registration flow — users are created via a simple admin call that requires no password. `LoginPage` needs a way to create a new user without navigating away. `CreateUserDialog` is a portal-style modal that handles the creation in-place and feeds the new user_id back to the login form.

## Upstream / Downstream

Rendered conditionally by `LoginPage` when the user clicks "Create New User". Not in the router — it is a component-level modal with no route.

Calls `api.createUser(userId, displayName?)` which maps to `POST /api/auth/create-user`. On success, calls `onCreated(userId)` (parent sets the login field) and auto-closes after 1.5 seconds via `setTimeout(onClose, 1500)`.

## Design decisions

**`pages/` placement for a modal.** This component is co-located with `LoginPage` rather than `components/ui/` because it is page-specific logic, not a reusable UI primitive. It should not be used from anywhere other than `LoginPage`.

**No automatic login after creation.** Unlike `RegisterPage`, this dialog does not log in the user after creating the account. The user still needs to enter their ID and click "Access Terminal". This is intentional — creating a user and logging in are two separate acts in local mode.

**Success state with auto-close delay.** A 1.5-second success display before `onClose` gives visual confirmation that creation succeeded before the modal disappears. The inputs are disabled during this window to prevent double-submission.

## Gotchas

**`setTimeout(onClose, 1500)` is not cleared on unmount.** If the parent component unmounts (e.g., the user navigates away) before the timeout fires, the callback calls `onClose` on an unmounted component. In React 18 strict mode this generates a warning but does not crash. A `useEffect` cleanup with `clearTimeout` would be the correct fix.

**`displayName` is passed as `undefined` if empty.** The API accepts an optional display name. The current code passes `displayName.trim() || undefined`, which means an all-whitespace display name is treated as absent. This is the intended behavior.
