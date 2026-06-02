---
code_file: frontend/src/lib/safe-return.ts
last_verified: 2026-06-01
stub: false
---

# safe-return.ts — open-redirect guard for the `?next=` post-login return path

## Why it exists

LoginPage reads a `?next=<path>` query param so `ProtectedRoute` can bounce an
unauthenticated visitor to `/login?next=<where-they-were-going>` and the login
flow can send them back afterwards. Without validation this is a textbook
open-redirect: a phishing link `https://agent.narra.nexus/login?next=https://evil.com/fake`
would, after a genuine login on the genuine host, navigate the now-trusting
user to evil.com. `isSafeReturnTo` exists solely to gate that — accept only
same-origin relative paths, reject anything that could escape the origin.

## Design decisions

**Whitelist by shape, not by URL parsing.** A value is safe iff it is
non-empty, starts with `/`, and does NOT start with `//` (protocol-relative
`//evil.com`) or `/\` (browsers that normalize `\` → `/`). There is no host
allowlist — the only safe answer is "a relative path on this origin," and a
cheap prefix check expresses exactly that.

**`:` is deliberately allowed after the first character.** Template-install
links carry an encoded URL in the query string
(`/app/templates/install?url=https%3A%2F%2Fwww.narra.nexus/...`). Once
`useSearchParams` decodes it, the embedded `https://` surfaces as literal text
in the query — harmless, because the browser only navigates to the path
portion; everything after `?` is just data handed to the page.

## Gotchas

The function is a TypeScript type guard (`next is string`): a truthy return
also narrows `next` from `string | null | undefined` to `string`, so callers
(`navigate(isSafeReturnTo(next) ? next : '/')`) use the value with no extra
non-null assertion.
