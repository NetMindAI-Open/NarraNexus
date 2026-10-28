/**
 * @file_name: browserLook.test.ts
 * @date: 2026-09-24
 * @description: browser_look output parsing across the three agent executors.
 *
 * The same tool result reaches the timeline in three shapes: NexusPower keeps
 * the metadata JSON verbatim; Claude Code concatenates the metadata JSON and
 * an image descriptor JSON; Codex wraps the MCP result (`content` array of
 * text / image parts). The card must read all three, and must decline (null)
 * anything it does not recognise so the shell's generic row takes over.
 */
import { describe, expect, it } from 'vitest';

import { parseBrowserLookOutput } from '../browserLook';

const META = {
  ok: true,
  outcome: 'OK',
  observation_id: 'view_1',
  url: 'https://example.com/chart',
  title: 'Quarterly chart',
  viewport: { width: 1280, height: 800, scroll_x: 0, scroll_y: 0, dpr: 2 },
  region: { x: 100, y: 50, width: 400, height: 300 },
  image: { width: 1200, height: 900, mime_type: 'image/png' },
  page_id: 'p1',
  session_id: 'agent_1',
};

describe('parseBrowserLookOutput', () => {
  it('reads the NexusPower shape (metadata JSON only)', () => {
    expect(parseBrowserLookOutput(JSON.stringify(META))).toEqual({
      ok: true,
      title: 'Quarterly chart',
      url: 'https://example.com/chart',
      image: { width: 1200, height: 900 },
      region: { width: 400, height: 300 },
    });
  });

  it('reads the Claude Code shape (metadata JSON + image descriptor JSON)', () => {
    const output = JSON.stringify(META) + '{"type": "image", "mime_type": "image/png", "base64_chars": 91234}';
    expect(parseBrowserLookOutput(output)?.image).toEqual({ width: 1200, height: 900 });
  });

  it('reads the Codex shape (MCP content array)', () => {
    const output = JSON.stringify({
      content: [
        { type: 'text', text: JSON.stringify(META) },
        { type: 'image', mime_type: 'image/png', base64_chars: 91234 },
      ],
      structured_content: null,
    });
    expect(parseBrowserLookOutput(output)?.title).toBe('Quarterly chart');
  });

  it('omits the region when the whole viewport was captured', () => {
    const whole = { ...META, region: { x: 0, y: 0, width: 1280, height: 800 } };
    expect(parseBrowserLookOutput(JSON.stringify(whole))?.region).toBeUndefined();
  });

  it('keeps braces inside strings from confusing the leading-object scan', () => {
    const tricky = { ...META, title: 'a } b { c "quoted"' };
    const output = JSON.stringify(tricky) + '{"type": "image"}';
    expect(parseBrowserLookOutput(output)?.title).toBe('a } b { c "quoted"');
  });

  it('reports a failed look with its message', () => {
    const output = JSON.stringify({ outcome: 'ERROR', message: 'Element is hidden; scroll it into view' });
    expect(parseBrowserLookOutput(output)).toEqual({
      ok: false,
      message: 'Element is hidden; scroll it into view',
    });
  });

  it.each([
    ['plain text', 'Browser operation failed'],
    ['empty', ''],
    ['unrelated JSON', '{"results": 12}'],
    ['truncated JSON', '{"outcome": "OK", "title": "cut'],
  ])('declines %s so the generic row renders', (_label, output) => {
    expect(parseBrowserLookOutput(output)).toBeNull();
  });
});
