/**
 * Native-dialog ban — a contract test, not a behaviour test.
 *
 * Tauri's wry webview does not render `window.alert` / `window.confirm` /
 * `window.prompt`. The call resolves falsy and NOTHING happens: an alert is
 * swallowed, and a confirm reads as "the user said no", so the handler behind it
 * bails out silently. Both break iron rule #7 (the DMG and the browser must
 * behave identically), and they break it invisibly — no error, no log.
 *
 * `useConfirm` (components/ui/ConfirmDialog) is the in-app replacement for both.
 *
 * This is deliberately a REPO-WIDE scan rather than a list of known offenders:
 * the offenders keep coming back. Two rounds of this bug (the subscription
 * confirms, then nine alerts) were each found by reading code, never by a test —
 * because the unit tests stubbed `window.confirm` and therefore proved nothing
 * about the platform where it is broken. A grep is the only assertion that
 * covers a file nobody has written yet.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { describe, expect, test } from 'vitest';

const SRC = resolve(__dirname, '../../');

/**
 * The equivalent ways to reach the same three globals. All are the CALL (or the
 * binding that enables it), so prose naming the APIs stays fine.
 *
 * A bare `alert(...)` is deliberately NOT banned: `useConfirm()` returns a
 * function literally named `alert`, so `const { alert } = useConfirm(); alert(…)`
 * is correct code that such a pattern would flag. The destructure-from-window
 * case below is what closes that gap from the other side.
 */
const NATIVE_DIALOG_PATTERNS = [
  /\b(window|globalThis|self)\s*\.\s*(alert|confirm|prompt)\s*\(/,
  // `const { alert } = window` / `= globalThis` — the call site then looks local.
  /\{[^}]*\b(alert|confirm|prompt)\b[^}]*\}\s*=\s*(window|globalThis|self)\b/,
];

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (entry === '__tests__' || entry === 'node_modules') continue;
      out.push(...sourceFiles(full));
      continue;
    }
    if (!/\.tsx?$/.test(entry) || /\.test\.tsx?$/.test(entry)) continue;
    if (/\.d\.ts$/.test(entry)) continue;
    out.push(full);
  }
  return out;
}

/** Offending `file:line` hits, skipping comment lines (this ban is discussed in
 * several file headers, and naming the API is how they explain themselves). */
function nativeDialogCalls(file: string): string[] {
  return readFileSync(file, 'utf-8')
    .split('\n')
    .map((line, i) => ({ line, no: i + 1 }))
    .filter(({ line }) => {
      const t = line.trim();
      if (t.startsWith('//') || t.startsWith('*') || t.startsWith('/*')) return false;
      return NATIVE_DIALOG_PATTERNS.some((re) => re.test(line));
    })
    .map(({ line, no }) => `${relative(SRC, file)}:${no}  ${line.trim()}`);
}

describe('no native dialogs (wry does not render them)', () => {
  test('no source file calls window.alert / confirm / prompt', () => {
    const hits = sourceFiles(SRC).flatMap(nativeDialogCalls);
    expect(
      hits,
      `Native dialogs are invisible on the desktop build. Use useConfirm() from ` +
        `components/ui instead — .alert() for a notice, .confirm() for a question.\n` +
        hits.join('\n'),
    ).toEqual([]);
  });
});
