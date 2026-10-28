/**
 * @file_name: chatDrafts.logout.test.ts
 * @description: A forced logout must not eat what the user was typing.
 *
 * When a session dies, `ProtectedRoute` unmounts the whole tree — the
 * Composer included. That teardown is exactly when a half-written message
 * is most likely to exist and least likely to have been sent. Drafts live
 * under their own localStorage key and the Composer flushes on unmount, so
 * they already survive; this test exists so a future logout-time "clear
 * everything" cleanup can't silently take them with it.
 */
import { beforeEach, describe, expect, test } from 'vitest';
import { getChatDraft, setChatDraft } from '../chatDrafts';
import { useConfigStore } from '@/stores/configStore';

describe('chat drafts across a forced logout', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  test('a draft written before logout is still there after', () => {
    useConfigStore.getState().login('alice', 'jwt', 'user');
    setChatDraft('agent_1', 'half-written question about the pipeline');

    useConfigStore.getState().logout();

    expect(getChatDraft('agent_1')).toBe('half-written question about the pipeline');
  });

  test('drafts survive per agent, not just for the active one', () => {
    useConfigStore.getState().login('alice', 'jwt', 'user');
    setChatDraft('agent_1', 'first');
    setChatDraft('agent_2', 'second');

    useConfigStore.getState().logout();

    expect(getChatDraft('agent_1')).toBe('first');
    expect(getChatDraft('agent_2')).toBe('second');
  });
});
