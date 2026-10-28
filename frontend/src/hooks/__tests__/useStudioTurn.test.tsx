/**
 * The studio's send path must not wait on the marketplace: while the
 * catalogue request hangs, Enter still produces an envelope (saying the
 * catalogue is unavailable) and one request is in flight, not one per turn.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

const h = vi.hoisted(() => ({
  searchMarketplaceSkills: vi.fn(),
  getAwareness: vi.fn(),
}));

vi.mock('@/lib/api', () => ({
  api: {
    searchMarketplaceSkills: (...a: unknown[]) => h.searchMarketplaceSkills(...a),
    getAwareness: (...a: unknown[]) => h.getAwareness(...a),
    updateAgent: vi.fn().mockResolvedValue({ success: true }),
    updateAwareness: vi.fn().mockResolvedValue({ success: true }),
  },
}));

import { useStudioTurn } from '../useStudioTurn';
import { useStudioStore } from '@/stores/studioStore';
import { useConfigStore } from '@/stores/configStore';
import { MARKETPLACE_SEARCH_TIMEOUT_MS } from '@/lib/apiTimeouts';

const AGENT = 'agent_x';

beforeEach(() => {
  h.searchMarketplaceSkills.mockReset();
  h.getAwareness.mockReset().mockResolvedValue({ awareness: 'existing instructions' });
  window.sessionStorage.clear();
  useStudioStore.setState({ open: {}, visited: {}, recommendations: {}, applyError: {} });
  useConfigStore.setState({ agents: [{ agent_id: AGENT, name: 'X', description: 'd' } as never] });
  useStudioStore.getState().openStudio(AGENT);
});

describe('useStudioTurn.applyFromReply', () => {
  it('a catalogue request that never settles does not block applying the reply forever', async () => {
    vi.useFakeTimers();
    try {
      h.searchMarketplaceSkills.mockReturnValue(new Promise(() => undefined)); // stalled
      const { result } = renderHook(() => useStudioTurn(AGENT));
      const reply = `ok<agent_draft>{"name":"Briefing","skill_ids":["web-search"],"channels":["telegram"]}</agent_draft>`;
      let settled = false;
      const applying = result.current.applyFromReply(reply).then(() => {
        settled = true;
      });
      // Text fields and channel suggestions land BEFORE the catalogue wait
      // resolves — neither depends on the marketplace.
      await vi.advanceTimersByTimeAsync(0);
      const { api } = await import('@/lib/api');
      expect(api.updateAgent).toHaveBeenCalledWith(AGENT, 'Briefing', 'd');
      expect(useStudioStore.getState().recommendations[AGENT]?.channels).toEqual(['telegram']);
      expect(settled).toBe(false);
      await vi.advanceTimersByTimeAsync(MARKETPLACE_SEARCH_TIMEOUT_MS + 3_000);
      await applying;
      expect(settled).toBe(true);
      // the skill suggestion stayed as it was (catalogue unknown → untouched),
      // and nothing was left hanging.
      expect(useStudioStore.getState().recommendations[AGENT]?.skill_ids ?? []).toEqual([]);
      expect(useStudioStore.getState().recommendations[AGENT]?.channels).toEqual(['telegram']);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('useStudioTurn.encodeOutgoing', () => {
  it('returns the envelope while the catalogue request is still hanging', async () => {
    h.searchMarketplaceSkills.mockReturnValue(new Promise(() => undefined)); // never settles
    const { result } = renderHook(() => useStudioTurn(AGENT));
    const out = await Promise.race([
      result.current.encodeOutgoing('build me a briefing agent'),
      new Promise<string>((_, reject) => setTimeout(() => reject(new Error('send path blocked')), 500)),
    ]);
    expect(out).toContain('build me a briefing agent');
    expect(out).toContain('"status":"unavailable"');
    expect(out).toContain('existing instructions');
  });

  it('keeps ONE catalogue request in flight across the mount effect and several sends', async () => {
    h.searchMarketplaceSkills.mockReturnValue(new Promise(() => undefined));
    const { result } = renderHook(() => useStudioTurn(AGENT));
    await result.current.encodeOutgoing('one');
    await result.current.encodeOutgoing('two');
    expect(h.searchMarketplaceSkills).toHaveBeenCalledTimes(1);
  });

  it('uses the catalogue once it has landed, and does not fetch again', async () => {
    h.searchMarketplaceSkills.mockResolvedValue({
      items: [{ skill_id: 'web-search', name: 'Web Search' }], total: 1,
    });
    const { result } = renderHook(() => useStudioTurn(AGENT));
    await new Promise((r) => setTimeout(r, 0)); // let the mount fetch land
    const out = await result.current.encodeOutgoing('go');
    expect(out).toContain('web-search');
    expect(out).toContain('"status":"known"');
    expect(h.searchMarketplaceSkills).toHaveBeenCalledTimes(1);
  });

  it('a failed fetch stays unknown and is retried on the next send', async () => {
    h.searchMarketplaceSkills.mockRejectedValueOnce(new Error('503'));
    h.searchMarketplaceSkills.mockResolvedValue({ items: [], total: 0 });
    const { result } = renderHook(() => useStudioTurn(AGENT));
    await new Promise((r) => setTimeout(r, 0));
    await result.current.encodeOutgoing('one'); // kicks off the retry
    await new Promise((r) => setTimeout(r, 0));
    const out = await result.current.encodeOutgoing('two');
    expect(out).toContain('"status":"known"');
    expect(h.searchMarketplaceSkills).toHaveBeenCalledTimes(2);
  });
});
