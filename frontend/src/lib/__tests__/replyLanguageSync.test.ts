/**
 * replyLanguageSync — isolated per test via vi.resetModules() + dynamic
 * import (precedent: chunkReload.test.ts), so the module-level guards
 * start clean every case (r3 #1: the old "no clobber" case was vacuous).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const listeners: Record<string, ((...a: unknown[]) => void)[]> = {};
vi.mock('@/i18n', () => ({
  default: {
    on: (ev: string, fn: (...a: unknown[]) => void) => {
      (listeners[ev] ??= []).push(fn);
    },
    resolvedLanguage: 'zh',
  },
  SUPPORTED_LANGUAGES: [{ code: 'zh' }, { code: 'en' }],
}));
vi.mock('@/lib/api', () => ({
  api: { getReplyLanguage: vi.fn(), setReplyLanguage: vi.fn() },
}));

import { api } from '@/lib/api';

const getMock = api.getReplyLanguage as ReturnType<typeof vi.fn>;
const setMock = api.setReplyLanguage as ReturnType<typeof vi.fn>;

async function freshInit() {
  vi.resetModules();
  const mod = await import('@/lib/replyLanguageSync');
  return mod.initReplyLanguageSync;
}

beforeEach(() => {
  for (const k in listeners) delete listeners[k];
  getMock.mockReset();
  setMock.mockReset().mockResolvedValue(undefined);
});

describe('initReplyLanguageSync', () => {
  it('backfills when server has no preference and detected lang is supported', async () => {
    getMock.mockResolvedValue(null);
    (await freshInit())('user_a');
    await vi.waitFor(() => expect(setMock).toHaveBeenCalledWith('zh'));
  });

  it('never clobbers an existing stored preference', async () => {
    getMock.mockResolvedValue('en');
    (await freshInit())('user_a');
    await vi.waitFor(() => expect(getMock).toHaveBeenCalled());
    expect(setMock).not.toHaveBeenCalled();
  });

  it('subscribes languageChanged once; backfill is per user; no identity -> no GET', async () => {
    getMock.mockResolvedValue(null);
    const init = await freshInit();
    init(null);                       // logout gap: nothing at all
    expect(getMock).not.toHaveBeenCalled();
    init('user_a');
    init('user_a');                   // same user twice: one GET
    await vi.waitFor(() => expect(getMock).toHaveBeenCalledTimes(1));
    init('user_b');                   // new user in same tab: fresh backfill
    await vi.waitFor(() => expect(getMock).toHaveBeenCalledTimes(2));
    expect(listeners['languageChanged']?.length).toBe(1);
    listeners['languageChanged'][0]('en');
    expect(setMock).toHaveBeenCalledWith('en');
  });
});
