/**
 * @file_name: mdEditSafety.test.ts
 * @description: The md block editor's loss guards (spec A §1.3, refined
 * 2026-08-19 after the Crepe round-trip spike): byte-exact round-trip does
 * not exist in the block-editor world (remark normalizes bullet chars, table
 * dashes), so the guard is SEMANTIC — frontmatter is extracted before the
 * editor ever sees the text (verbatim re-attach on save), and editability
 * requires AST equivalence between the original body and the editor's
 * serialization (a structure-losing parse disables editing, style
 * normalization does not).
 */

import { describe, expect, it } from 'vitest';
import { extractFrontmatter, mdAstEqual } from '../mdEditSafety';

describe('extractFrontmatter', () => {
  it('splits a YAML frontmatter block off verbatim', () => {
    const doc = '---\ntitle: x\ntags: [a, b]\n---\n\n# Doc\n\nbody\n';
    const { frontmatter, body } = extractFrontmatter(doc);
    expect(frontmatter).toBe('---\ntitle: x\ntags: [a, b]\n---\n');
    expect(body).toBe('\n# Doc\n\nbody\n');
    expect(frontmatter + body).toBe(doc); // verbatim reassembly
  });

  it('returns empty frontmatter when there is none', () => {
    const doc = '# Doc\n\nbody\n';
    expect(extractFrontmatter(doc)).toEqual({ frontmatter: '', body: doc });
  });

  it('a mid-document thematic break is NOT frontmatter', () => {
    const doc = 'intro\n\n---\n\nafter\n';
    expect(extractFrontmatter(doc).frontmatter).toBe('');
  });

  it('an unterminated opening fence is not frontmatter', () => {
    const doc = '---\ntitle: x\n\n# no closing fence\n';
    expect(extractFrontmatter(doc).frontmatter).toBe('');
  });
});

describe('mdAstEqual', () => {
  it('style normalization is equivalent: bullet char', () => {
    expect(mdAstEqual('- a\n- b\n', '* a\n* b\n')).toBe(true);
  });

  it('style normalization is equivalent: table separator dashes', () => {
    expect(mdAstEqual('| a |\n| --- |\n| 1 |\n', '| a |\n| - |\n| 1 |\n')).toBe(true);
  });

  it('losing a construct is NOT equivalent', () => {
    expect(mdAstEqual('text with `code`\n', 'text with code\n')).toBe(false);
  });

  it('changed text content is NOT equivalent', () => {
    expect(mdAstEqual('# Title\n', '# Titel\n')).toBe(false);
  });

  it('identical documents are equivalent', () => {
    const doc = '# 报告\n\n中文段落,含**加粗**。\n\n```python\nprint("hi")\n```\n';
    expect(mdAstEqual(doc, doc)).toBe(true);
  });
});

describe('extractFrontmatter — CRLF (review #334 I7)', () => {
  it('splits CRLF frontmatter verbatim and reassembles byte-exact', () => {
    const doc = '---\r\ntitle: x\r\n---\r\n\r\n# Doc\r\n\r\nbody\r\n';
    const { frontmatter, body } = extractFrontmatter(doc);
    expect(frontmatter).toBe('---\r\ntitle: x\r\n---\r\n');
    expect(frontmatter + body).toBe(doc);
  });

  it('CRLF without frontmatter is all body', () => {
    const doc = '# Doc\r\n\r\nbody\r\n';
    expect(extractFrontmatter(doc)).toEqual({ frontmatter: '', body: doc });
  });

  it('a blank line inside a CRLF opener is not frontmatter', () => {
    const doc = '---\r\ntitle: x\r\n\r\n# no closing fence\r\n';
    expect(extractFrontmatter(doc).frontmatter).toBe('');
  });
});

describe('extractFrontmatter — mixed line endings (review #334 r2 I2)', () => {
  it('an LF frontmatter survives a CRLF line pasted into the body', () => {
    const doc = '---\ntitle: x\n---\n\n# Doc\r\n\r\npasted crlf body\r\n';
    const { frontmatter, body } = extractFrontmatter(doc);
    expect(frontmatter).toBe('---\ntitle: x\n---\n');
    expect(frontmatter + body).toBe(doc);
  });

  it('a CRLF frontmatter survives an LF line in the body', () => {
    const doc = '---\r\ntitle: x\r\n---\r\nlf body line\nmore\n';
    const { frontmatter, body } = extractFrontmatter(doc);
    expect(frontmatter).toBe('---\r\ntitle: x\r\n---\r\n');
    expect(frontmatter + body).toBe(doc);
  });
});

describe('extractFrontmatter — mixed eol INSIDE the fence block (review #334 r3 I2)', () => {
  it('an LF opener with a CRLF closing fence still splits, byte-exact', () => {
    const doc = '---\ntitle: x\r\n---\r\nbody\n';
    const { frontmatter, body } = extractFrontmatter(doc);
    expect(frontmatter).toBe('---\ntitle: x\r\n---\r\n');
    expect(frontmatter + body).toBe(doc);
  });

  it('a CRLF opener with an LF closing fence splits too', () => {
    const doc = '---\r\ntitle: x\n---\n\n# Doc\n';
    const { frontmatter, body } = extractFrontmatter(doc);
    expect(frontmatter).toBe('---\r\ntitle: x\n---\n');
    expect(frontmatter + body).toBe(doc);
  });

  it('a pathological \\r\\r\\n fence is NOT frontmatter (strip removes one \\r)', () => {
    const doc = '---\r\r\ntitle: x\r\r\n---\r\r\nbody\n';
    expect(extractFrontmatter(doc).frontmatter).toBe('');
  });
});
