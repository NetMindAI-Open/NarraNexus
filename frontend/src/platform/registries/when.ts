/**
 * @file_name: when.ts
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: The `when` predicate grammar for slot-point entries (spec §658): a closed vocabulary, evaluated by the host.
 *
 * A slot entry may carry `when: WhenClause | WhenClause[]` (AND). Exactly
 * three clause kinds exist — `conversationKind:<kind>`, `agentHas:<module>`,
 * `setting:<key>` — each negatable with a leading `!`. Anything else is a
 * parse error at registration time, so a typo never reads as "always
 * visible". The host builds the `WhenContext` from its stores
 * (`platform/whenContext.ts`); plugins never evaluate predicates themselves.
 */

export type WhenClause = string;

export interface WhenContext {
  /** The conversation the surface belongs to (`chat`, `team`, a plugin kind); undefined outside a conversation. */
  conversationKind?: string;
  /** Module class names the current agent has instances of (e.g. `JobModule`). */
  agentModules?: readonly string[];
  /** Host settings a predicate may read (`setting:<key>` is truthy when the value is truthy). */
  settings?: Readonly<Record<string, unknown>>;
}

const CLAUSE = /^(!?)(conversationKind|agentHas|setting):([A-Za-z0-9_.:-]+)$/;

export function parseWhen(when: WhenClause | WhenClause[] | undefined): { negate: boolean; kind: string; arg: string }[] {
  if (when === undefined) return [];
  const clauses = Array.isArray(when) ? when : [when];
  return clauses.map((clause) => {
    const m = CLAUSE.exec(clause.trim());
    if (!m) throw new Error(`invalid when clause "${clause}": expected conversationKind:<kind> | agentHas:<module> | setting:<key> (optionally prefixed with !)`);
    return { negate: m[1] === '!', kind: m[2], arg: m[3] };
  });
}

/** True when every clause holds (an empty `when` always holds). */
export function evaluateWhen(when: WhenClause | WhenClause[] | undefined, ctx: WhenContext): boolean {
  for (const { negate, kind, arg } of parseWhen(when)) {
    let holds: boolean;
    if (kind === 'conversationKind') holds = ctx.conversationKind === arg;
    else if (kind === 'agentHas') holds = (ctx.agentModules ?? []).includes(arg);
    else holds = Boolean(ctx.settings?.[arg]);
    if (negate ? holds : !holds) return false;
  }
  return true;
}
