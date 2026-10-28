/**
 * @file_name: apiTimeouts.ts
 * @author: NetMind.AI
 * @date: 2026-09-04
 * @description: Request time limits shared between `api.ts` (which puts them
 * on the fetch as `AbortSignal.timeout`) and the callers that need to derive
 * their own waiting ceiling from the same number.
 *
 * Its own module, not a named export of `api.ts`: tests mock `@/lib/api`
 * wholesale (the module is one big class), and a constant living there would
 * be missing from every such mock. Keeping the number here means a hook can
 * import it without dragging the whole client through the mock boundary, and
 * there is still exactly ONE copy of it.
 */

/**
 * Marketplace search is a plain read, but the creation studio pulls it on
 * every turn and dedups the in-flight request: a fetch that never settles
 * (a stalled connection, not a 5xx) would otherwise sit in the browser's
 * per-origin connection pool for minutes and, on HTTP/1.1 localhost, starve
 * every other request behind it. Aborting makes it a normal failure.
 */
export const MARKETPLACE_SEARCH_TIMEOUT_MS = 10_000;
