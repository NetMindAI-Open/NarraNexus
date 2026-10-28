/**
 * api.installPlugin — parses the `POST /api/plugins/{id}/install` ndjson
 * stream. Bypasses `request<T>` because the body is line-delimited, not one
 * JSON document, so this exercises the manual `getReader()` loop directly
 * rather than any shared fetch helper.
 */
import { afterEach, describe, expect, test, vi } from 'vitest';
import { api, ApiError } from '../api';
import type { PluginInstallEvent } from '@/types';

function streamFromLines(lines: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const line of lines) {
        controller.enqueue(encoder.encode(line + '\n'));
      }
      controller.close();
    },
  });
}

afterEach(() => vi.restoreAllMocks());

describe('api.installPlugin', () => {
  test('invokes onEvent once per ndjson line, in order, and resolves with the final frame', async () => {
    const events: PluginInstallEvent[] = [
      { done: false, phase: 'pip', line: 'Collecting openai-codex-cli-bin' },
      { done: false, phase: 'pip', line: 'Installing collected packages' },
      {
        done: true,
        ok: true,
        error: null,
        status: {
          id: 'codex_cli',
          display_name: 'Codex CLI',
          installed: true,
          version: '1.0.0',
          target_version: '1.0.0',
          update_available: false,
          logged_in: false,
          size_hint: '120MB',
          busy: false,
        },
      },
    ];
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      body: streamFromLines(events.map((e) => JSON.stringify(e))),
    } as unknown as Response);

    const received: PluginInstallEvent[] = [];
    const final = await api.installPlugin('codex_cli', (e) => received.push(e));

    expect(received).toEqual(events);
    expect(final).toEqual(events[2]);
  });

  test('a line split across two stream chunks is still parsed as one event', async () => {
    const encoder = new TextEncoder();
    const finalEvent: PluginInstallEvent = { done: true, ok: true, error: null, status: null };
    const fullLine = JSON.stringify(finalEvent);
    const splitAt = Math.floor(fullLine.length / 2);
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(fullLine.slice(0, splitAt)));
        controller.enqueue(encoder.encode(fullLine.slice(splitAt) + '\n'));
        controller.close();
      },
    });
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      body: stream,
    } as unknown as Response);

    const received: PluginInstallEvent[] = [];
    const final = await api.installPlugin('codex_cli', (e) => received.push(e));

    expect(received).toEqual([finalEvent]);
    expect(final).toEqual(finalEvent);
  });

  test('a non-2xx response throws ApiError instead of attempting to stream', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      status: 403,
      statusText: 'Forbidden',
      body: null,
      json: async () => ({ detail: 'plugins are cloud-managed' }),
    } as unknown as Response);

    await expect(api.installPlugin('codex_cli', () => {})).rejects.toThrow(ApiError);
    await expect(api.installPlugin('codex_cli', () => {})).rejects.toThrow(
      /plugins are cloud-managed/,
    );
  });
});
