/**
 * @file_name: slotPoints.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: Slot-point registries validate `when` at registration, order and filter entries, pick the owning message renderer, and SlotOutlet isolates a crashing plugin component.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { Registry } from '@/platform/registries/registry';
import { CHAT_HEADER_ACTIONS, rendererFor, visibleSlotEntries, type MessageRendererDef, type SlotComponentDef } from '@/platform/registries/slotPoints';
import { SlotOutlet } from '@/platform/SlotOutlet';

const errorSink = vi.hoisted(() => ({ reportUiError: vi.fn() }));
vi.mock('@/platform/errorSink', () => errorSink);

describe('slot points', () => {
  it('rejects an invalid when clause at registration', () => {
    expect(() => CHAT_HEADER_ACTIONS.register('bad', { label: 'x', when: 'nope:x', run: () => {} }, { owner: 'acme.t' })).toThrow(/invalid when clause/);
    expect(CHAT_HEADER_ACTIONS.has('bad')).toBe(false);
  });

  it('filters by when and sorts by order', () => {
    const reg = new Registry<SlotComponentDef>('ui.test');
    const C = () => null;
    reg.register('late', { component: C, order: 50 }, { owner: 'a' });
    reg.register('team-only', { component: C, when: 'conversationKind:team', order: 5 }, { owner: 'a' });
    reg.register('early', { component: C, order: 10 }, { owner: 'a' });
    expect(visibleSlotEntries(reg.list(), { conversationKind: 'chat' }).map((e) => e.id)).toEqual(['early', 'late']);
    expect(visibleSlotEntries(reg.list(), { conversationKind: 'team' }).map((e) => e.id)).toEqual(['team-only', 'early', 'late']);
  });

  it('rendererFor picks the lowest-order match and survives a throwing matcher', () => {
    const reg = new Registry<MessageRendererDef>('ui.r');
    const A = () => null;
    reg.register('throws', { match: () => { throw new Error('x'); }, component: A }, { owner: 'a' });
    reg.register('b', { match: (m) => (m as { content: string }).content.startsWith('hi'), component: A, order: 20 }, { owner: 'a' });
    reg.register('c', { match: (m) => (m as { content: string }).content.startsWith('hi'), component: A, order: 10 }, { owner: 'a' });
    expect(rendererFor(reg.list(), { content: 'hi there' })).toBe(reg.get('c'));
    expect(rendererFor(reg.list(), { content: 'nope' })).toBeUndefined();
  });

  it('SlotOutlet renders visible entries and isolates a crashing one', () => {
    const reg = new Registry<SlotComponentDef>('ui.outlet');
    reg.register('ok', { component: ({ agentId }) => <span>ok for {agentId}</span> }, { owner: 'acme.ok' });
    reg.register('boom', { component: () => { throw new Error('render boom'); } }, { owner: 'acme.boom' });
    reg.register('hidden', { component: () => <span>hidden</span>, when: 'setting:never' }, { owner: 'acme.h' });
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<SlotOutlet registry={reg} ctx={{ settings: {} }} agentId="a1" as="div" />);
    spy.mockRestore();
    expect(screen.getByText('ok for a1')).toBeInTheDocument();
    expect(screen.queryByText('hidden')).toBeNull();
    expect(errorSink.reportUiError).toHaveBeenCalledWith(expect.any(Error), expect.objectContaining({ source: 'acme.boom' }));
  });
});
