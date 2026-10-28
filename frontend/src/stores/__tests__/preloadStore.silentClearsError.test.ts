/**
 * @file_name: preloadStore.silentClearsError.test.ts
 * @description: A SUCCESSFUL silent refresh must clear the previous error.
 *
 * Bug (2026-09-03, creation studio): a fresh agent has no AwarenessModule
 * instance, so `GET /awareness` legitimately answers
 * `success: false, error: "Awareness data not found for agent: X"` — and the
 * non-silent first load stored that error. The studio then WROTE awareness and
 * called `refreshAwareness(agentId, true)`. The silent branch set the new
 * content but never touched `awarenessError`, and AwarenessPanel renders the
 * error BEFORE the content — so the panel kept showing "Awareness data not
 * found" over text it already had, until a full page reload re-ran the
 * non-silent path.
 *
 * Invariant: success obsoletes the previous failure, silently or not.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

vi.mock('@/lib/api', () => ({
  api: {
    getAgentInbox: vi.fn(),
    getJobs: vi.fn(),
    getAwareness: vi.fn(),
    getSocialNetworkList: vi.fn(),
    getChatHistory: vi.fn(),
    getCosts: vi.fn(),
  },
}));

import { api } from '@/lib/api';
import { usePreloadStore } from '../preloadStore';

beforeEach(() => {
  vi.clearAllMocks();
  usePreloadStore.setState({ awareness: null, awarenessError: null, awarenessLoading: false });
});

describe('silent refresh vs a stale error', () => {
  it('a fresh agent\'s failed read stores the error', async () => {
    vi.mocked(api.getAwareness).mockResolvedValue({
      success: false,
      error: 'Awareness data not found for agent: agt_1',
    } as never);

    await usePreloadStore.getState().refreshAwareness('agt_1');

    expect(usePreloadStore.getState().awarenessError).toContain('not found');
    expect(usePreloadStore.getState().awareness).toBeNull();
  });

  it('a successful SILENT refresh clears it — this is the studio-write case', async () => {
    usePreloadStore.setState({ awarenessError: 'Awareness data not found for agent: agt_1' });
    vi.mocked(api.getAwareness).mockResolvedValue({
      success: true,
      awareness: '## Role\nMorning market brief',
    } as never);

    await usePreloadStore.getState().refreshAwareness('agt_1', true);

    expect(usePreloadStore.getState().awareness).toBe('## Role\nMorning market brief');
    expect(usePreloadStore.getState().awarenessError).toBeNull();
  });

  it('clears the error even when the CONTENT is unchanged', async () => {
    // The silent branch skips set() when nothing changed. A stale error must
    // still be cleared, or an unchanged-but-successful poll leaves the panel
    // showing a failure that no longer applies.
    usePreloadStore.setState({ awareness: 'same', awarenessError: 'boom' });
    vi.mocked(api.getAwareness).mockResolvedValue({
      success: true,
      awareness: 'same',
    } as never);

    await usePreloadStore.getState().refreshAwareness('agt_1', true);

    expect(usePreloadStore.getState().awareness).toBe('same');
    expect(usePreloadStore.getState().awarenessError).toBeNull();
  });

  it('a silent FAILURE still leaves existing data and error alone', async () => {
    // Silent means "do not disturb the UI": a transient failed poll must not
    // blank content the user is reading, nor invent an error banner.
    usePreloadStore.setState({ awareness: 'kept', awarenessError: null });
    vi.mocked(api.getAwareness).mockResolvedValue({ success: false, error: 'flaky' } as never);

    await usePreloadStore.getState().refreshAwareness('agt_1', true);

    expect(usePreloadStore.getState().awareness).toBe('kept');
    expect(usePreloadStore.getState().awarenessError).toBeNull();
  });
});
