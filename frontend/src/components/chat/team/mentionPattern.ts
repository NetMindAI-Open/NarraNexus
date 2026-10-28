/**
 * @file_name: mentionPattern.ts
 * @author: NarraNexus
 * @date: 2026-08-14
 * @description: What counts as an @mention, in one place.
 *
 * There are two renderers — one for plain text (the user's own messages, which
 * do not go through markdown) and one for the markdown AST (everything an agent
 * says). Both need the same answer, and they had a hand-copied regex each, in
 * the same folder, under a comment insisting the two must match.
 *
 * The pattern also has to match the SERVER's `_extract_team_mentions` and the
 * composer's autocomplete character for character. Highlighting someone who
 * will not actually be woken — or missing someone who will — is worse than not
 * highlighting at all, because it teaches the reader to distrust the highlight.
 * Keeping one literal here does not enforce that across the language boundary,
 * but it removes the copy that was drifting on this side of it.
 */

/** `@` followed by word characters or CJK. Global: callers drive it with exec. */
export const MENTION_PATTERN = /@([\w一-鿿]+)/g;

/** A fresh matcher — `lastIndex` is stateful, so a shared instance is a bug. */
export function mentionMatcher(): RegExp {
  return new RegExp(MENTION_PATTERN.source, 'g');
}

/**
 * The lowercased @tokens in a piece of text.
 *
 * The SEND path's entry point. It exists here rather than in the composer
 * because "what counts as an @token" is the one thing the send path and the two
 * renderers must never disagree about: the highlighted names and the woken
 * agents come from the same sentence, and a reader who sees three names lit up
 * and two teammates answer has no way to tell which half is wrong.
 *
 * What happens next is shared too — see `matchMembers`. It was not, once: the
 * renderers matched member names exactly while the send path matched first names
 * and prefixes, on the stated grounds that a loose highlight would light up
 * email addresses. That reasoning was wrong (the loose rule is a prefix match,
 * and `example` is not a prefix of any member), and the divergence it defended
 * meant a teammate could be woken without the room showing they had been named.
 */
export function mentionTokens(text: string): Set<string> {
  const out = new Set<string>();
  const re = mentionMatcher();
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) out.add(m[1].toLowerCase());
  return out;
}

/**
 * Which member display names these tokens address.
 *
 * The ONE rule, mirroring the server's `_extract_team_mentions` line for line:
 * exact name, first word, or any token of two characters or more that the name
 * starts with. Loose on purpose — someone typing `@ana` for "Ana Silva" means
 * her, and waking nobody is a worse answer than waking her.
 *
 * It has to be one rule because highlighting and waking are answers to the same
 * question asked by two different surfaces. They were separate, and the gap was
 * reachable through the product's own autocomplete: it inserts `@Ana Silva`, the
 * token pattern stops at the space, so the token is `ana` — the server woke Ana
 * and the room rendered her name as ordinary text. The person being addressed
 * could not see that they had been addressed, which is the exact failure every
 * comment in this folder calls worse than no highlight at all.
 *
 * `@all` / `@everyone` are not here: they address the room rather than a member,
 * and the two callers do different things with that (the composer sends
 * `"@all"`, the renderers highlight the word). `isAddressed` folds them in.
 */
export function matchMembers(tokens: Set<string>, names: Iterable<string>): string[] {
  const out: string[] = [];
  for (const raw of names) {
    const nm = (raw || '').toLowerCase();
    if (!nm) continue;
    const first = nm.split(/\s+/)[0] || nm;
    if (
      tokens.has(nm) ||
      tokens.has(first) ||
      [...tokens].some((t) => t.length >= 2 && nm.startsWith(t))
    ) {
      out.push(raw);
    }
  }
  return out;
}

/**
 * Does this word address someone who will actually be reached?
 *
 * Derived from `matchMembers` rather than agreeing with it by convention: the
 * highlight must light up exactly the people the send path wakes, and "these two
 * are kept in sync" is a promise that has already been broken once here.
 *
 * Stays a `(word, names) => boolean` shape deliberately — it runs per text node
 * per message, and anything heavier would give back the render cost that
 * memoising the member map just bought.
 */
export function isAddressed(word: string, names: Set<string>): boolean {
  const lower = word.toLowerCase();
  if (lower === 'all' || lower === 'everyone') return true;
  return matchMembers(new Set([lower]), names).length > 0;
}
