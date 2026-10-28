/**
 * @file_name: builderProtocol.ts
 * @author: NetMind.AI
 * @date: 2026-09-03
 * @description: The wire format between the creation studio's chat and its
 * configuration panel.
 *
 * Outbound: every message the user sends while the studio is open is wrapped
 * with the builder instruction plus the target agent's CURRENT configuration.
 * Restating the config each turn is what makes the user's own manual edits
 * authoritative — the model sees what the panel actually holds, so it revises
 * instead of overwriting.
 *
 * Inbound: the model appends exactly one `<agent_draft>` block. The frontend
 * strips it before render, parses it, validates it against a whitelist, and
 * applies the field diff to the real agent.
 *
 * Three failure modes are handled here because they are not hypothetical —
 * they are what a weaker model actually does, and binding rule #15 says the
 * platform does not police the user's choice of model:
 *
 *   1. Streaming shows an OPEN tag long before the close tag arrives, so the
 *      strip must also kill an unterminated block. Matching only the closed
 *      form scrolls raw JSON past the reader on every single turn.
 *   2. CLI-style models emit real newlines inside JSON strings when the value
 *      is Markdown. A second parse attempt escapes control characters inside
 *      string literals only — object structure still has to be valid JSON.
 *   3. Anything unrecognised is dropped rather than trusted: skill ids not in
 *      the caller's catalogue, channels outside the supported set, missing
 *      fields. Fail-closed, and a parse failure degrades to "no config change
 *      this turn" — it never interrupts the conversation.
 *   4. The instruction below hands the model an EMPTY skeleton as the shape to
 *      copy, so a model that copies it verbatim is a normal turn, not an
 *      anomaly. Empty text therefore means "not touching this field", never
 *      "blank it" — losing the user's instructions is unrecoverable, and the
 *      panel holds no backup of what was there.
 */
import { AGENT_TEXT_MAX_LENGTH } from '@/lib/agentLimits';

/** Opening tag of the inbound config block. */
export const DRAFT_OPEN = '<agent_draft>';
/** Closing tag of the inbound config block. */
export const DRAFT_CLOSE = '</agent_draft>';

/** Marker pair wrapping the outbound instruction + config envelope. */
export const TURN_OPEN = '[NARRANEXUS_AGENT_BUILDER]';
export const TURN_CLOSE = '[/NARRANEXUS_AGENT_BUILDER]';

/**
 * Channel types the panel can actually surface.
 *
 * The draft only ever states INTENT ("the user wants Telegram") — binding a
 * channel needs a credential, which travels user → backend and must never
 * enter this envelope. Anything outside this set is dropped.
 */
export const SUPPORTED_CHANNELS = [
  'telegram',
  'lark',
  'slack',
  'wechat',
  'discord',
  'narra_messenger',
  'home_assistant',
] as const;

export type SupportedChannel = (typeof SUPPORTED_CHANNELS)[number];

export interface AgentDraft {
  name: string;
  description: string;
  awareness: string;
  skill_ids: string[];
  channels: SupportedChannel[];
}

/**
 * One catalogue entry as the model sees it. id + name only: the envelope is
 * restated on EVERY turn, and a description per skill multiplied by the
 * catalogue size is kilobytes of prompt the user pays for each message. The
 * name is what a marketplace shows in a list; a model that needs more asks.
 */
export interface SkillOption {
  id: string;
  name: string;
}

/**
 * The skill catalogue as known to this turn.
 *
 * `null` means "not known" — the fetch failed or has not landed — and is
 * distinct from an empty list, which means "known, and there is nothing".
 * The distinction matters downstream: an unknown catalogue must not reject
 * every proposed id (and wipe the recommendations already accepted), while an
 * empty one correctly rejects them all.
 */
export interface SkillCatalogue {
  items: SkillOption[];
  /** Total entries on the server; larger than `items.length` when cut. */
  total: number;
}

/** An empty draft — also the shape the merge falls back to field by field. */
export function emptyDraft(): AgentDraft {
  return { name: '', description: '', awareness: '', skill_ids: [], channels: [] };
}

// ── outbound ────────────────────────────────────────────────────────────

const INSTRUCTION = `You are acting as the NarraNexus Agent Builder for this conversation.
Help the user design ONE practical agent. Reply in the user's language.

Prefer producing a reasonable draft immediately over interrogating the user.
Ask at most two focused questions per turn, and only questions that materially
change behaviour. When the request is already complete, ask nothing.

Every reply MUST end with exactly one block, and nothing after it:
${DRAFT_OPEN}{"name":"","description":"","awareness":"","skill_ids":[],"channels":[]}${DRAFT_CLOSE}

Rules for that block:
- Valid, compact JSON on ONE physical line. No Markdown fences around it.
- Escape every line break inside a string as \\n. NEVER emit a literal
  newline inside a JSON string.
- Preserve good existing values from CURRENT CONFIG unless the user asks for a
  change. The user edits the panel directly, so CURRENT CONFIG is the truth —
  revise it, do not overwrite it.
- name is concise and suitable for a sidebar list.
- description is ONE sentence, at most 200 characters. It is read by OTHER
  AGENTS deciding whether to route work here, not by humans.
- awareness is the agent's future instructions, in Markdown: role, workflow,
  output, constraints. Write instructions, not a report about them.
- skill_ids may only contain ids listed in AVAILABLE SKILLS.
- channels may only contain values from SUPPORTED CHANNELS, and states intent
  only — the user supplies every credential themselves.
- Never request, expose, or place secrets, tokens, passwords, or environment
  variable values anywhere in the block.

The interface applies the block to the agent and shows the result in the
configuration panel. Do not claim you changed anything the panel does not
show, and do not paste the block's contents into your prose.`;

/**
 * Build the message actually sent while the studio is open.
 *
 * The instruction and the config envelope are wrapped in markers that
 * MessageBubble strips, so the user's own bubble shows only their sentence.
 * Returns null for an empty request — an envelope with no request would leave
 * the model guessing.
 */
export function encodeBuilderTurn(input: {
  request: string;
  current: AgentDraft;
  catalogue: SkillCatalogue | null;
}): string | null {
  const request = input.request.trim();
  if (!request) return null;
  const envelope = {
    current_config: input.current,
    available_skills: describeCatalogue(input.catalogue),
    supported_channels: SUPPORTED_CHANNELS,
  };
  return [
    TURN_OPEN,
    INSTRUCTION,
    '',
    'CURRENT CONFIG / AVAILABLE SKILLS / SUPPORTED CHANNELS:',
    JSON.stringify(envelope),
    TURN_CLOSE,
    '',
    request,
  ].join('\n');
}

/**
 * The catalogue as the envelope states it. A cut catalogue SAYS it is cut —
 * "first N of M" — so the model knows an absent skill may still exist rather
 * than silently never recommending anything past the page. An unknown
 * catalogue says so too, and the merge ignores skill_ids that turn.
 */
function describeCatalogue(catalogue: SkillCatalogue | null): {
  status: 'known' | 'unavailable';
  items: SkillOption[];
  note?: string;
} {
  if (!catalogue) {
    return {
      status: 'unavailable',
      items: [],
      note: 'The skill catalogue could not be loaded this turn; do not propose skill_ids.',
    };
  }
  const shown = catalogue.items.length;
  if (catalogue.total > shown) {
    return {
      status: 'known',
      items: catalogue.items,
      note: `Showing the first ${shown} of ${catalogue.total} skills; others exist but cannot be proposed by id this turn.`,
    };
  }
  return { status: 'known', items: catalogue.items };
}

/**
 * Recover what the user typed, for rendering their bubble.
 *
 * Safe on every message: content without the markers is returned unchanged.
 * The envelope is only ever the PREFIX of a message (see encodeBuilderTurn),
 * so both patterns are anchored to the start — a user who types the marker
 * literally in their own sentence keeps the rest of the sentence. An
 * unterminated envelope at the start is dropped through to the end so a
 * truncated marker cannot leak the instruction into the bubble.
 */
export function decodeBuilderTurn(content: string): string {
  if (!content.startsWith(TURN_OPEN)) return content;
  const open = escapeForRegExp(TURN_OPEN);
  const close = escapeForRegExp(TURN_CLOSE);
  return content
    .replace(new RegExp(`^${open}[\\s\\S]*?${close}`), '')
    .replace(new RegExp(`^${open}[\\s\\S]*$`), '')
    .trim();
}

// ── inbound ─────────────────────────────────────────────────────────────

/**
 * Remove the config block from assistant text before rendering.
 *
 * TWO patterns, and the second one is the important one: during streaming the
 * open tag exists for many frames before the close tag does. Matching only
 * the closed form means the raw JSON scrolls past the reader every turn.
 */
export function stripAgentDraft(content: string): string {
  if (!content.includes(DRAFT_OPEN)) return content;
  const open = escapeForRegExp(DRAFT_OPEN);
  const close = escapeForRegExp(DRAFT_CLOSE);
  return content
    .replace(new RegExp(`\\s*${open}[\\s\\S]*?${close}`, 'g'), '')
    .replace(new RegExp(`\\s*${open}[\\s\\S]*$`), '')
    .trim();
}

/**
 * Parse the LAST complete config block, or null.
 *
 * Last, not first: a model that restates the block has the freshest state at
 * the end. Null covers "no block yet" (mid-stream) and "hopelessly broken" —
 * both mean the same thing to the caller, which is "do not change the config
 * this turn".
 */
export function parseAgentDraft(content: string): Record<string, unknown> | null {
  const pattern = new RegExp(
    `${escapeForRegExp(DRAFT_OPEN)}([\\s\\S]*?)${escapeForRegExp(DRAFT_CLOSE)}`,
    'g',
  );
  let body: string | null = null;
  for (const m of content.matchAll(pattern)) body = m[1];
  if (body === null) return null;

  const candidate = body.trim();
  if (!candidate) return null;

  const parsed = tryParseObject(candidate);
  if (parsed) return parsed;
  // Second attempt: only control characters INSIDE string literals are
  // escaped. Structure and the rest of the syntax must still be valid JSON —
  // we are tolerating a known model habit, not writing a JSON repair tool.
  return tryParseObject(escapeControlCharsInStrings(candidate));
}

/**
 * Validate a parsed block against the current config and the catalogues.
 *
 * Every text field falls back to its current value: a model that omits a
 * field, sends the wrong type, or sends an EMPTY string must not blank the
 * user's configuration. Empty is treated as "not touching this field" because
 * the instruction itself shows the model an all-empty skeleton to copy, and a
 * copied skeleton must be a no-op turn rather than a wipe of name,
 * description and instructions at once. Each field is judged on its own —
 * a turn that fills only `awareness` and leaves `name` empty is normal.
 *
 * name and description are additionally cut to the server's column width. A
 * too-long value usually means the model DID mean to change the field and was
 * merely verbose, so cutting keeps the intent where falling back would make
 * the user feel ignored. Cutting here, not at write time, keeps `next` equal
 * to what gets written — otherwise every later diff would see a change and
 * re-send the same 422 forever. awareness is long text and is not cut.
 *
 * skill_ids: `null` for the catalogue means "the catalogue is not known yet"
 * (the fetch failed or has not landed), and the proposed ids are left alone
 * as the current recommendations rather than filtered to nothing — a catalogue
 * that IS known and simply does not list an id still rejects it. channels are
 * intersected with the compile-time set, which is always known.
 */
export function mergeAgentDraft(
  current: AgentDraft,
  parsed: Record<string, unknown> | null,
  availableSkillIds: string[] | null,
): AgentDraft {
  if (!parsed) return current;
  const allowedChannels = new Set<string>(SUPPORTED_CHANNELS);
  const allowedSkills = availableSkillIds === null ? null : new Set(availableSkillIds);

  return {
    name: takeText(parsed.name, current.name, AGENT_TEXT_MAX_LENGTH),
    description: takeText(parsed.description, current.description, AGENT_TEXT_MAX_LENGTH),
    awareness: takeText(parsed.awareness, current.awareness),
    skill_ids:
      allowedSkills === null
        ? current.skill_ids
        : takeList(parsed.skill_ids, current.skill_ids, (v) => allowedSkills.has(v)),
    channels: takeList(parsed.channels, current.channels, (v) =>
      allowedChannels.has(v),
    ) as SupportedChannel[],
  };
}

// ── internals ───────────────────────────────────────────────────────────

/**
 * A trimmed, non-empty string, cut to `maxLength` when given; anything else —
 * wrong type, empty, whitespace only — yields the fallback. Whitespace-only
 * counts as empty because it reads as empty everywhere the value is shown.
 * Trimmed before the cut so the stored value is what the sidebar shows, and
 * a cut never leaves a lone high surrogate (half an emoji) at the end.
 */
function takeText(value: unknown, fallback: string, maxLength?: number): string {
  if (typeof value !== 'string') return fallback;
  const text = value.trim();
  if (text === '') return fallback;
  if (maxLength === undefined || text.length <= maxLength) return text;
  return text.slice(0, maxLength).replace(/[\uD800-\uDBFF]$/, '');
}

function takeList<T extends string>(
  value: unknown,
  fallback: T[],
  allowed: (v: string) => boolean,
): T[] {
  if (!Array.isArray(value)) return fallback;
  const seen = new Set<string>();
  const out: T[] = [];
  for (const item of value) {
    if (typeof item !== 'string') continue;
    const v = item.trim();
    if (!v || seen.has(v) || !allowed(v)) continue;
    seen.add(v);
    out.push(v as T);
  }
  return out;
}

function tryParseObject(text: string): Record<string, unknown> | null {
  try {
    const value: unknown = JSON.parse(text);
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    return value as Record<string, unknown>;
  } catch {
    return null;
  }
}

/** Escape raw control characters that sit INSIDE JSON string literals. */
function escapeControlCharsInStrings(json: string): string {
  let out = '';
  let inString = false;
  let escaped = false;
  for (const ch of json) {
    if (escaped) {
      out += ch;
      escaped = false;
      continue;
    }
    if (ch === '\\') {
      out += ch;
      escaped = true;
      continue;
    }
    if (ch === '"') {
      inString = !inString;
      out += ch;
      continue;
    }
    if (inString) {
      if (ch === '\n') { out += '\\n'; continue; }
      if (ch === '\r') { out += '\\r'; continue; }
      if (ch === '\t') { out += '\\t'; continue; }
    }
    out += ch;
  }
  return out;
}

function escapeForRegExp(literal: string): string {
  return literal.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
