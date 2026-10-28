/**
 * @file_name: builtinTabIds.ts
 * @author: Bin Liang
 * @date: 2026-09-07
 * @description: The shell's own rail tab ids — the one literal list, typed so shell code cannot misspell one.
 *
 * The strip itself is DERIVED from the `PANELS` registry (`tabs.ts`), and a
 * plugin may register any id, so the open type stays `AtomicTabId = string`.
 * What the shell writes by hand — its own registrations in
 * `platform/builtin.ts`, the chat header's fixed menu groups — is typed
 * `BuiltinTabId`, so a typo there is a compile error rather than a
 * silently empty drawer. `builtin.test.ts` pins that the registry's
 * shell- and feature-owned builtin strip tabs are exactly this list.
 */
export const BUILTIN_TAB_IDS = [
  'builder',
  'awareness',
  'workspace',
  'channels',
  'smarthome',
  'jobs',
  'browser',
  'inbox',
  'artifacts',
  'memory',
  'social',
  'skills',
  'mcp',
] as const;

export type BuiltinTabId = (typeof BUILTIN_TAB_IDS)[number];
