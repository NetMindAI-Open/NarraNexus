/**
 * Contract tests for the studio's wire format.
 *
 * The three cases worth defending, in order of how badly they bite:
 *   1. an UNTERMINATED draft block during streaming — miss it and raw JSON
 *      scrolls past the reader on every turn;
 *   2. real newlines inside JSON strings — what a CLI-style model emits when
 *      the value is Markdown;
 *   3. values outside the catalogue — must be dropped, never trusted.
 */
import { describe, test, expect } from 'vitest';
import {
  DRAFT_CLOSE,
  DRAFT_OPEN,
  TURN_CLOSE,
  TURN_OPEN,
  emptyDraft,
  encodeBuilderTurn,
  decodeBuilderTurn,
  mergeAgentDraft,
  parseAgentDraft,
  stripAgentDraft,
  type AgentDraft,
} from '../builderProtocol';

const SKILLS = [{ id: 'web-search', name: 'Web Search' }];
const CATALOGUE = { items: SKILLS, total: SKILLS.length };

function draftBlock(obj: unknown): string {
  return `${DRAFT_OPEN}${JSON.stringify(obj)}${DRAFT_CLOSE}`;
}

describe('encodeBuilderTurn / decodeBuilderTurn', () => {
  test('the user request survives verbatim and comes last', () => {
    const msg = encodeBuilderTurn({
      request: '每天早上给我一份金融晨报',
      current: emptyDraft(),
      catalogue: CATALOGUE,
    });
    expect(msg).not.toBeNull();
    expect(msg!.endsWith('每天早上给我一份金融晨报')).toBe(true);
    expect(decodeBuilderTurn(msg!)).toBe('每天早上给我一份金融晨报');
  });

  test('the envelope restates the current config, so manual edits win', () => {
    const current: AgentDraft = {
      name: '我手改的名字',
      description: 'd',
      awareness: 'a',
      skill_ids: ['web-search'],
      channels: ['telegram'],
    };
    const msg = encodeBuilderTurn({ request: '改一下', current, catalogue: CATALOGUE })!;
    expect(msg).toContain('我手改的名字');
    expect(msg).toContain('web-search');
  });

  test('refuses an empty request', () => {
    expect(encodeBuilderTurn({ request: '  ', current: emptyDraft(), catalogue: null })).toBeNull();
  });

  test('decode leaves ordinary messages untouched and drops a truncated envelope', () => {
    expect(decodeBuilderTurn('普通消息')).toBe('普通消息');
    expect(decodeBuilderTurn(`${TURN_OPEN}\nhalf an instruction`)).toBe('');
    expect(decodeBuilderTurn(`${TURN_OPEN}x${TURN_CLOSE}\nreq`)).toBe('req');
  });

  test('a marker the USER typed inside their own sentence is not eaten', () => {
    // The envelope is only ever the prefix, so the strip is anchored there.
    const typed = `what does ${TURN_OPEN} mean? and this part too`;
    expect(decodeBuilderTurn(typed)).toBe(typed);
    const msg = encodeBuilderTurn({ request: typed, current: emptyDraft(), catalogue: CATALOGUE })!;
    expect(decodeBuilderTurn(msg)).toBe(typed);
  });

  test('a cut catalogue tells the model it is cut; an unknown one says so too', () => {
    const cut = encodeBuilderTurn({
      request: 'r',
      current: emptyDraft(),
      catalogue: { items: SKILLS, total: 200 },
    })!;
    expect(cut).toContain('first 1 of 200');
    const unknown = encodeBuilderTurn({ request: 'r', current: emptyDraft(), catalogue: null })!;
    expect(unknown).toContain('"status":"unavailable"');
    const full = encodeBuilderTurn({ request: 'r', current: emptyDraft(), catalogue: CATALOGUE })!;
    const envelope = JSON.parse(full.split('\n').find((l) => l.startsWith('{'))!) as {
      available_skills: Record<string, unknown>;
    };
    expect(envelope.available_skills.status).toBe('known');
    expect('note' in envelope.available_skills).toBe(false);
  });
});

describe('stripAgentDraft', () => {
  test('removes a closed block', () => {
    const reply = `配好了。\n${draftBlock({ name: 'x' })}`;
    expect(stripAgentDraft(reply)).toBe('配好了。');
  });

  test('removes an UNTERMINATED block — the streaming case', () => {
    const midStream = `配好了。\n${DRAFT_OPEN}{"name":"金融晨报","awareness":"## 角色`;
    expect(stripAgentDraft(midStream)).toBe('配好了。');
  });

  test('removes the open tag the instant it appears, before any payload', () => {
    expect(stripAgentDraft(`好了。${DRAFT_OPEN}`)).toBe('好了。');
  });

  test('removes every block when a model restates it', () => {
    const two = `a${draftBlock({ n: 1 })}b${draftBlock({ n: 2 })}`;
    expect(stripAgentDraft(two)).toBe('ab');
  });

  test('leaves ordinary replies untouched', () => {
    expect(stripAgentDraft('没有块的普通回复')).toBe('没有块的普通回复');
    expect(stripAgentDraft('')).toBe('');
  });
});

describe('parseAgentDraft', () => {
  test('reads a well-formed block', () => {
    const got = parseAgentDraft(`x${draftBlock({ name: '金融晨报' })}`);
    expect(got).toEqual({ name: '金融晨报' });
  });

  test('takes the LAST block — a restated block is the freshest', () => {
    const two = `${draftBlock({ name: 'old' })}${draftBlock({ name: 'new' })}`;
    expect(parseAgentDraft(two)).toEqual({ name: 'new' });
  });

  test('falls back for REAL newlines inside a string (the CLI-model habit)', () => {
    const broken =
      `${DRAFT_OPEN}{"name":"晨报","awareness":"## 角色\n每天早上产出晨报\n\n## 约束\n不做投资建议"}${DRAFT_CLOSE}`;
    const got = parseAgentDraft(broken);
    expect(got).not.toBeNull();
    expect(String(got!.awareness)).toContain('## 约束');
    expect(String(got!.awareness)).toContain('\n');
  });

  test('also tolerates raw tabs and carriage returns inside strings', () => {
    const broken = `${DRAFT_OPEN}{"awareness":"a\tb\r\nc"}${DRAFT_CLOSE}`;
    expect(parseAgentDraft(broken)).not.toBeNull();
  });

  test('returns null with no block, an unterminated block, or garbage', () => {
    expect(parseAgentDraft('没有块')).toBeNull();
    expect(parseAgentDraft(`${DRAFT_OPEN}{"name":"x"`)).toBeNull();
    expect(parseAgentDraft(`${DRAFT_OPEN}not json at all${DRAFT_CLOSE}`)).toBeNull();
    expect(parseAgentDraft(`${DRAFT_OPEN}${DRAFT_CLOSE}`)).toBeNull();
  });

  test('rejects a non-object payload — an array must not read as a config', () => {
    expect(parseAgentDraft(`${DRAFT_OPEN}[1,2]${DRAFT_CLOSE}`)).toBeNull();
    expect(parseAgentDraft(`${DRAFT_OPEN}"just a string"${DRAFT_CLOSE}`)).toBeNull();
  });
});

describe('mergeAgentDraft', () => {
  const current: AgentDraft = {
    name: 'cur-name',
    description: 'cur-desc',
    awareness: 'cur-aware',
    skill_ids: ['web-search'],
    channels: ['telegram'],
  };

  test('a null parse changes nothing — the turn simply does not touch config', () => {
    expect(mergeAgentDraft(current, null, ['web-search'])).toEqual(current);
  });

  test('missing fields keep their current value, never blank out', () => {
    const got = mergeAgentDraft(current, { name: 'new' }, ['web-search']);
    expect(got.name).toBe('new');
    expect(got.description).toBe('cur-desc');
    expect(got.awareness).toBe('cur-aware');
    expect(got.skill_ids).toEqual(['web-search']);
    expect(got.channels).toEqual(['telegram']);
  });

  test('wrong types fall back rather than propagate', () => {
    const got = mergeAgentDraft(current, { name: 42, skill_ids: 'web-search' }, ['web-search']);
    expect(got.name).toBe('cur-name');
    expect(got.skill_ids).toEqual(['web-search']);
  });

  test('skill ids outside the catalogue are dropped', () => {
    const got = mergeAgentDraft(current, { skill_ids: ['web-search', 'not-a-real-skill'] }, [
      'web-search',
    ]);
    expect(got.skill_ids).toEqual(['web-search']);
  });

  test('unsupported channels are dropped', () => {
    const got = mergeAgentDraft(current, { channels: ['telegram', 'carrier-pigeon'] }, []);
    expect(got.channels).toEqual(['telegram']);
  });

  test('an explicit empty list IS honoured — that is how you remove things', () => {
    const got = mergeAgentDraft(current, { skill_ids: [], channels: [] }, ['web-search']);
    expect(got.skill_ids).toEqual([]);
    expect(got.channels).toEqual([]);
  });

  test('EMPTY strings fall back — a copied skeleton must not wipe the agent', () => {
    // The instruction shows the model an all-empty skeleton as the shape to
    // copy; a weaker model copies it verbatim. That turn must be a no-op.
    const got = mergeAgentDraft(current, { name: '', description: '', awareness: '' }, ['web-search']);
    expect(got.name).toBe('cur-name');
    expect(got.description).toBe('cur-desc');
    expect(got.awareness).toBe('cur-aware');
  });

  test('whitespace-only text counts as empty', () => {
    const got = mergeAgentDraft(current, { name: '   ', awareness: '\n\t' }, []);
    expect(got.name).toBe('cur-name');
    expect(got.awareness).toBe('cur-aware');
  });

  test('each text field is judged on its own — one filled, one empty is normal', () => {
    const got = mergeAgentDraft(current, { name: '', awareness: 'new instructions' }, []);
    expect(got.name).toBe('cur-name');
    expect(got.awareness).toBe('new instructions');
  });

  test('name and description are cut to the server column width; awareness is not', () => {
    const long = 'x'.repeat(400);
    const got = mergeAgentDraft(current, { name: long, description: long, awareness: long }, []);
    expect(got.name).toHaveLength(255);
    expect(got.description).toHaveLength(255);
    expect(got.awareness).toHaveLength(400);
  });

  test('text is stored trimmed, and a cut never leaves half an emoji', () => {
    const got = mergeAgentDraft(current, { name: '  Morning Brief  ', awareness: '  a  ' }, []);
    expect(got.name).toBe('Morning Brief');
    expect(got.awareness).toBe('a');
    // 254 ASCII chars + an emoji (two UTF-16 units) straddles the 255 cut.
    const straddle = 'x'.repeat(254) + '😀';
    const cut = mergeAgentDraft(current, { name: straddle }, []).name;
    expect(cut).toBe('x'.repeat(254));
    expect(cut).not.toMatch(/[\uD800-\uDBFF]$/);
  });

  test('an UNKNOWN catalogue leaves skill recommendations untouched', () => {
    // null = the fetch failed or has not landed. Filtering against nothing
    // would reject every id and persist the wipe over accepted ones.
    const got = mergeAgentDraft(current, { skill_ids: ['web-search', 'other'] }, null);
    expect(got.skill_ids).toEqual(['web-search']);
    expect(mergeAgentDraft(current, { skill_ids: [] }, null).skill_ids).toEqual(['web-search']);
  });

  test('a KNOWN empty catalogue still rejects every id', () => {
    expect(mergeAgentDraft(current, { skill_ids: ['web-search'] }, []).skill_ids).toEqual([]);
  });

  test('duplicates and blanks are squeezed out', () => {
    const got = mergeAgentDraft(current, { skill_ids: ['web-search', 'web-search', ' '] }, [
      'web-search',
    ]);
    expect(got.skill_ids).toEqual(['web-search']);
  });
});
