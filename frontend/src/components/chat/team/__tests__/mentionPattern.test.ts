/**
 * @file_name: mentionPattern.test.ts
 * @description: The highlighted names and the woken agents come from one rule.
 *
 * Three places in this folder answer a question about `@`: the plain-text
 * renderer (the user's own messages), the markdown AST renderer (everything an
 * agent says), and the composer's send path. Each had its own hand-copied
 * regex, under comments insisting they must match.
 *
 * The failure that costs the most is not a missing highlight — it is the two
 * halves DISAGREEING. A reader who sees three names lit up and two teammates
 * answer cannot tell which half is wrong, and neither can the person debugging
 * it. So the tokens come from one place.
 *
 * The rule for WHO a token addresses is shared too, and it was not always: the
 * renderers matched exact names while the send path (and the server) matched
 * first names and prefixes. That gap was reachable through the product's own
 * autocomplete — it inserts `@Ana Silva`, the pattern stops at the space, the
 * token is `ana` — so Ana was woken and her name rendered as ordinary text. The
 * person being addressed could not see that she had been, which every comment
 * in this folder calls worse than no highlight at all.
 *
 * An earlier version of this file asserted that divergence as intended, on the
 * grounds that a loose highlight would light up email addresses. It would not:
 * the loose rule is a PREFIX match, and `example` is not a prefix of any member.
 * The justification was false and the test was pinning the bug.
 */
import { describe, expect, test } from 'vitest';

import {
  isAddressed,
  matchMembers,
  mentionMatcher,
  mentionTokens,
} from '../mentionPattern';

describe('mentionTokens', () => {
  test('picks out the @words, lowercased', () => {
    expect(mentionTokens('hey @Bruno and @Ana')).toEqual(new Set(['bruno', 'ana']));
  });

  test('accepts CJK names', () => {
    // The character class is shared with the server's `_extract_team_mentions`;
    // a name this side cannot see is a teammate the user thinks they addressed.
    expect(mentionTokens('@小明 看一下')).toEqual(new Set(['小明']));
  });

  test('an email address is one token, not a mention of its domain', () => {
    // It still TOKENISES — deciding it is not a person is the next question's
    // job, and the two renderers answer it differently from the send path.
    expect(mentionTokens('mail me at bin@example.com')).toEqual(new Set(['example']));
  });

  test('no @ means no tokens', () => {
    expect(mentionTokens('nothing to see')).toEqual(new Set());
  });

  test('a repeated mention counts once', () => {
    expect(mentionTokens('@ana @ana @ana')).toEqual(new Set(['ana']));
  });
});

describe('the matcher is not shared state', () => {
  test('two matchers do not inherit each other lastIndex', () => {
    // A module-level global regex would: `exec` advances `lastIndex`, so the
    // second caller starts mid-string and silently misses the first mention.
    const a = mentionMatcher();
    a.exec('@ana @bruno');
    const b = mentionMatcher();
    expect(b.exec('@ana @bruno')?.[1]).toBe('ana');
  });

  test('calling mentionTokens twice gives the same answer', () => {
    const text = '@ana and @bruno';
    expect(mentionTokens(text)).toEqual(mentionTokens(text));
  });
});

describe('one rule for who is addressed', () => {
  const members = ['Ana Silva', 'Bruno'];
  const names = new Set(members.map((n) => n.toLowerCase()));

  test('@all and @everyone address the room', () => {
    expect(isAddressed('all', names)).toBe(true);
    expect(isAddressed('everyone', names)).toBe(true);
  });

  test('an email domain addresses nobody', () => {
    expect(isAddressed('example', names)).toBe(false);
    expect(matchMembers(new Set(['example']), members)).toEqual([]);
  });

  test('a first name addresses the member who owns it — on BOTH sides', () => {
    // The case that was broken, and it needed no typo to reach: the composer's
    // own autocomplete inserts `@Ana Silva`, the token pattern stops at the
    // space, so the token is `ana`. The send path woke her; the renderer, which
    // used a stricter rule, drew her name as ordinary text. The person being
    // addressed could not see that she had been.
    expect(matchMembers(new Set(['ana']), members)).toEqual(['Ana Silva']);
    expect(isAddressed('ana', names)).toBe(true);
  });

  test('a prefix of two characters or more counts, one does not', () => {
    // Mirrors the server's `_extract_team_mentions`. A single letter would
    // match half the roster.
    expect(matchMembers(new Set(['an']), members)).toEqual(['Ana Silva']);
    expect(matchMembers(new Set(['a']), members)).toEqual([]);
  });

  test('the full name matches too', () => {
    expect(matchMembers(new Set(['ana silva']), members)).toEqual(['Ana Silva']);
  });

  test('an ambiguous first name matches every owner, not a guess', () => {
    // Two people called Ana: waking both is the honest answer, and the
    // highlight has to agree with it rather than pick one.
    const both = ['Ana Silva', 'Ana Turner'];
    expect(matchMembers(new Set(['ana']), both)).toEqual(both);
    expect(isAddressed('ana', new Set(both.map((n) => n.toLowerCase())))).toBe(true);
  });

  test('highlighting and waking cannot drift apart', () => {
    // The property the shared rule exists for, stated as one assertion: for
    // every token, "does this light up" and "does this wake someone" are the
    // same answer.
    for (const token of ['ana', 'ana silva', 'bruno', 'br', 'example', 'zzz']) {
      expect(isAddressed(token, names)).toBe(
        matchMembers(new Set([token]), members).length > 0,
      );
    }
  });
});
