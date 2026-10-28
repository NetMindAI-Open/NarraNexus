/**
 * M-5: `useWhenContext` used to select the WHOLE config store object as `settings`
 * (`useConfigStore((s) => s as unknown as Record<string, unknown>)`), so ANY `set()` on the
 * store — even one touching a field no `setting:<key>` clause reads — produced a new `settings`
 * reference, defeating the `useMemo` and re-rendering every `useWhenContext` consumer
 * (ChatHeader, Composer, MessageBubble, Sidebar, TopBar, every AgentRow) on every store change.
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { useWhenContext } from '@/platform/whenContext';
import { useConfigStore } from '@/stores';

const initialState = useConfigStore.getState();
afterEach(() => {
  useConfigStore.setState(initialState, true);
});

describe('useWhenContext (M-5)', () => {
  it('a store update to a non-whitelisted key does not change the returned context object identity', () => {
    const { result } = renderHook(() => useWhenContext({ conversationKind: 'chat', agentId: null }));
    const before = result.current;
    act(() => {
      // netmindToken is not in SETTING_KEYS — a real, unrelated store field.
      useConfigStore.getState().setNetmindToken('some-token');
    });
    expect(result.current).toBe(before);
  });
});
