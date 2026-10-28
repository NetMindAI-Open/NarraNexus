/**
 * @file_name: turnTimelineToolRenderer.test.tsx
 * @date: 2026-09-24
 * @description: A tool renderer registered under a bare tool name owns that tool's output row; everything else keeps the generic row.
 *
 * Tool names arrive namespaced (`mcp__<server>__<tool>`); the registry is
 * keyed by the bare name so a plugin never depends on which MCP server id a
 * deployment mounted its tool under. A renderer that throws, or that
 * declines (renders null), must leave the generic row in place — the output
 * text is never lost behind a broken or partial plugin.
 */
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { TurnTimeline } from '../TurnTimeline';
import { TOOL_RENDERERS } from '@/platform/registries';
import { onUiError, resetErrorSink } from '@/platform/errorSink';
import type { TurnEvent } from '@/types';

const disposers: (() => void)[] = [];
afterEach(() => {
  disposers.splice(0).forEach((d) => d());
  resetErrorSink();
});

const output = (id: string, tool_name: string, text: string) =>
  ({ id, ts: 1, type: 'tool_output', tool_name, output: text }) as TurnEvent;

describe('tool output renderers', () => {
  it('a renderer owns its tool output row, matched on the bare name', () => {
    disposers.push(TOOL_RENDERERS.register('acme_look', {
      component: ({ output: text, toolName }) => <div data-testid="custom">{toolName}:{text}</div>,
    }, { owner: 'acme.tools' }));
    render(<TurnTimeline events={[
      output('o1', 'mcp__acme_module__acme_look', 'seen'),
      output('o2', 'mcp__other__read_file', 'file body'),
    ]} />);
    expect(screen.getByTestId('custom')).toHaveTextContent('mcp__acme_module__acme_look:seen');
    // The unrelated tool keeps the generic, collapsed output row.
    expect(screen.getByText('read_file')).toBeInTheDocument();
    expect(screen.queryByText('file body')).toBeNull();
  });

  it('a renderer that throws falls back to the generic row and reports the owner', () => {
    disposers.push(TOOL_RENDERERS.register('acme_look', {
      component: () => { throw new Error('boom'); },
    }, { owner: 'acme.broken_tools' }));
    const seen: unknown[] = [];
    onUiError((r) => seen.push(r));
    render(<TurnTimeline events={[output('o1', 'mcp__acme_module__acme_look', 'seen')]} />);
    expect(screen.getByText('acme_look')).toBeInTheDocument();
    expect(seen).toEqual([expect.objectContaining({ source: 'acme.broken_tools' })]);
  });

  it('a renderer can decline an output it does not recognise', () => {
    disposers.push(TOOL_RENDERERS.register('acme_look', {
      component: ({ output: text }) => (text === 'mine' ? <div data-testid="custom">mine</div> : null),
      accepts: (text) => text === 'mine',
    }, { owner: 'acme.tools' }));
    render(<TurnTimeline events={[output('o1', 'mcp__acme_module__acme_look', 'something else')]} />);
    expect(screen.queryByTestId('custom')).toBeNull();
    expect(screen.getByText('acme_look')).toBeInTheDocument();
  });
});

describe('the builtin browser_look renderer', () => {
  it('shows the generic row while its chunk loads, then the summary card', async () => {
    // The shell registers browser_look lazily (builtin.ts keeps components out
    // of the first chunk); the row must never be blank in between.
    await import('@/platform/builtin');
    const look = JSON.stringify({
      outcome: 'OK', title: 'Quarterly chart', url: 'https://example.com',
      viewport: { width: 1280, height: 800 }, region: { x: 0, y: 0, width: 1280, height: 800 },
      image: { width: 1568, height: 980 },
    });
    render(<TurnTimeline events={[output('o1', 'mcp__browser_module__browser_look', look)]} />);
    expect(await screen.findByText('Looked at the page')).toBeInTheDocument();
    expect(screen.getByText('1568×980')).toBeInTheDocument();
    expect(screen.queryByText('area 1280×800')).toBeNull(); // whole viewport → no area label
  });
});
