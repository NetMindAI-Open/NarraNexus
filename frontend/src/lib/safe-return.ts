/**
 * Validates a post-login return path passed via `?next=` on /login.
 *
 * The single concern is open-redirect: a phishing link of the form
 * `https://agent.narra.nexus/login?next=https://evil.com/fake` will,
 * without validation, send the user to evil.com after they log in on the
 * real domain — they trust the original host bar and never notice the
 * post-login navigation. Accepting only same-origin relative paths
 * defeats that.
 *
 * Returns true iff `next` is a relative path on the same origin:
 *   - non-empty
 *   - starts with "/"
 *   - does NOT start with "//"  (protocol-relative: //evil.com/...)
 *   - does NOT start with "/\\" (some browsers normalize \\ → /)
 *
 * Note we deliberately allow ":" anywhere after the first character so
 * paths like /app/templates/install?url=https%3A%2F%2Fwebsite.narra.nexus/...
 * pass validation. After useSearchParams decodes, the embedded URL
 * surfaces as literal "https://..." in the query string — harmless,
 * since the browser only navigates to the path portion (everything
 * after "?" is just data passed to the page).
 */
export function isSafeReturnTo(next: string | null | undefined): next is string {
  if (!next) return false;
  if (!next.startsWith("/")) return false;
  if (next.startsWith("//")) return false;
  if (next.startsWith("/\\")) return false;
  return true;
}
