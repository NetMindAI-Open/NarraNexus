/**
 * The diff is the contract: an unchanged field must issue NO request.
 * Without it every reply re-PUTs the same instructions and the agent's
 * update timestamp churns on turns that changed nothing.
 *
 * Second contract: a failed write is reported, never thrown — the platform
 * must not become the thing that interrupts a working conversation.
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';
import type { AgentDraft } from '../builderProtocol';

const updateAgent = vi.fn();
const updateAwareness = vi.fn();
const getAwareness = vi.fn();

vi.mock('@/lib/api', () => ({
  api: {
    updateAgent: (...a: unknown[]) => updateAgent(...a),
    updateAwareness: (...a: unknown[]) => updateAwareness(...a),
    getAwareness: (...a: unknown[]) => getAwareness(...a),
  },
}));

const { applyLiveFields, readCurrentConfig } = await import('../builderApply');

const base: AgentDraft = {
  name: 'n',
  description: 'd',
  awareness: 'a',
  skill_ids: [],
  channels: [],
};

beforeEach(() => {
  updateAgent.mockReset().mockResolvedValue({ success: true });
  updateAwareness.mockReset().mockResolvedValue({ success: true });
  getAwareness.mockReset().mockResolvedValue({ awareness: 'from-server' });
});

describe('applyLiveFields', () => {
  test('an identical draft issues no requests at all', async () => {
    const out = await applyLiveFields('agt', base, { ...base });
    expect(updateAgent).not.toHaveBeenCalled();
    expect(updateAwareness).not.toHaveBeenCalled();
    expect(out.changed).toEqual([]);
    expect(out.errors).toEqual([]);
  });

  test('a name change writes identity only', async () => {
    const out = await applyLiveFields('agt', base, { ...base, name: 'new' });
    expect(updateAgent).toHaveBeenCalledWith('agt', 'new', 'd');
    expect(updateAwareness).not.toHaveBeenCalled();
    expect(out.changed).toEqual(['identity']);
  });

  test('an instructions change writes awareness only', async () => {
    const out = await applyLiveFields('agt', base, { ...base, awareness: 'new' });
    expect(updateAwareness).toHaveBeenCalledWith('agt', 'new');
    expect(updateAgent).not.toHaveBeenCalled();
    expect(out.changed).toEqual(['awareness']);
  });

  test('skills and channels are NEVER written — they are recommendations', async () => {
    await applyLiveFields('agt', base, {
      ...base,
      skill_ids: ['web-search'],
      channels: ['telegram'],
    });
    expect(updateAgent).not.toHaveBeenCalled();
    expect(updateAwareness).not.toHaveBeenCalled();
  });

  test('a rejected write is reported, not thrown', async () => {
    updateAwareness.mockResolvedValue({ success: false, message: 'nope' });
    const out = await applyLiveFields('agt', base, { ...base, awareness: 'x' });
    expect(out.changed).toEqual([]);
    expect(out.errors).toHaveLength(1);
    expect(out.errors[0]).toContain('nope');
  });

  test('one failed field does not stop the other', async () => {
    updateAgent.mockRejectedValue(new Error('boom'));
    const out = await applyLiveFields('agt', base, { ...base, name: 'x', awareness: 'y' });
    expect(out.changed).toEqual(['awareness']);
    expect(out.errors).toHaveLength(1);
  });
});

describe('readCurrentConfig', () => {
  test('identity comes from the caller, instructions from the API', async () => {
    const got = await readCurrentConfig(
      'agt',
      { name: 'N', description: 'D' },
      { skill_ids: ['s'], channels: ['telegram'] },
    );
    expect(got).toEqual({
      name: 'N',
      description: 'D',
      awareness: 'from-server',
      skill_ids: ['s'],
      channels: ['telegram'],
    });
  });

  test('a failed instructions read degrades to empty rather than blocking', async () => {
    getAwareness.mockRejectedValue(new Error('offline'));
    const got = await readCurrentConfig('agt', { name: 'N', description: '' }, { skill_ids: [], channels: [] });
    expect(got.awareness).toBe('');
    expect(got.name).toBe('N');
  });
});
