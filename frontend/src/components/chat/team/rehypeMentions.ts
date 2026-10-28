/**
 * @file_name: rehypeMentions.ts
 * @author: NarraNexus
 * @date: 2026-08-14
 * @description: Highlighting @mentions without touching code.
 *
 * An agent @mentioning a teammate IS the handoff, so the addressee has to be
 * able to spot it without reading every message. The first version did that by
 * rewriting the markdown SOURCE — a `String.replace` over the whole body before
 * it was parsed.
 *
 * That put a literal `<span data-testid="mention-all" class="…">@all</span>`
 * inside every code block containing `@all`, `@everyone` or a teammate's name —
 * and a team room's main output is code and commands, where `@all` is a real
 * make target, a shell argument, and another IM's mention syntax. Markdown
 * escapes HTML inside a fence, so it was never a security problem; it was worse
 * in a mundane way: what the user copied out of the room was broken, and the
 * first suspicion would fall on the model rather than on the renderer.
 *
 * A regex cannot know what a code block is. The AST does, so the replacement
 * happens there — and code is excluded by NOT DESCENDING into it, rather than by
 * asking each text node who its parent is. The two answers differ as soon as
 * anything sits in between, which raw HTML (`rehypeRaw` is on) and any token
 * highlighter both do.
 *
 * The pattern lives in `mentionPattern`, shared with the plain-text renderer for
 * the user's own messages. It must also stay identical to the server's
 * `_extract_team_mentions` and the composer's autocomplete — highlighting
 * someone who will not actually be woken (or missing someone who will) is worse
 * than not highlighting at all.
 */

import { SKIP, visit } from 'unist-util-visit';
import type { Node, Parent } from 'unist';

import { isAddressed, mentionMatcher } from './mentionPattern';

interface HastText extends Node {
  type: 'text';
  value: string;
}

interface HastElement extends Parent {
  type: 'element';
  tagName: string;
  properties?: Record<string, unknown>;
}

/**
 * A rehype plugin that wraps team @mentions in a highlight span.
 *
 * @param names - Lowercased member display names. `@all` / `@everyone` are always
 *   recognised; any other word is resolved by `matchMembers` — the same rule the
 *   send path and the server use, so what lights up is exactly who gets woken.
 *   A word that addresses nobody stays plain, or an email address and a
 *   decorative `@` would light up and teach the reader to ignore the highlight.
 */
export function rehypeMentions(names: Set<string>) {
  return () => (tree: Node) => {
    visit(tree, (node: Node, index: number | undefined, parent: Parent | undefined) => {
      // Code is skipped by NOT DESCENDING into it, rather than by checking each
      // text node's immediate parent. The two differ the moment anything sits
      // between: `<pre><span>@all</span></pre>` from raw HTML (rehypeRaw is on,
      // so a model can emit it), or a highlighter that wraps tokens. Whatever
      // the nesting, nothing under a `code`/`pre` is reached at all.
      if (node.type === 'element') {
        const tag = (node as HastElement).tagName;
        if (tag === 'code' || tag === 'pre') return SKIP;
        return;
      }
      if (node.type !== 'text') return;
      if (!parent || index === undefined) return;

      const value = (node as HastText).value;
      const re = mentionMatcher();
      const out: Node[] = [];
      let last = 0;
      let m: RegExpExecArray | null;
      while ((m = re.exec(value)) !== null) {
        const word = m[1];
        if (!isAddressed(word, names)) continue;
        if (m.index > last) out.push({ type: 'text', value: value.slice(last, m.index) } as HastText);
        out.push({
          type: 'element',
          tagName: 'span',
          properties: {
            'data-testid': `mention-${word}`,
            className:
              'rounded px-0.5 font-medium text-[var(--color-carbon)] bg-[var(--nm-paper-warm)]',
          },
          children: [{ type: 'text', value: m[0] } as HastText],
        } as HastElement);
        last = m.index + m[0].length;
      }
      if (!out.length) return;
      if (last < value.length) {
        out.push({ type: 'text', value: value.slice(last) } as HastText);
      }
      parent.children.splice(index, 1, ...out);
      // Resume AFTER the nodes just inserted, so the walk does not re-enter the
      // spans it created and match their contents again.
      return index + out.length;
    });
  };
}
