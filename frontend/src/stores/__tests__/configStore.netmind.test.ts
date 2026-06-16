import { beforeEach, describe, expect, test } from 'vitest';
import { useConfigStore } from '../configStore';

beforeEach(() => {
  useConfigStore.getState().logout();
});

describe('configStore NetMind fields', () => {
  test('login stores profile; setNetmindToken stores token', () => {
    useConfigStore.getState().login('uSysCode', 'jwt', 'user', {
      displayName: 'Alice', email: 'a@b.com',
    });
    useConfigStore.getState().setNetmindToken('nm-tok');
    const s = useConfigStore.getState();
    expect(s.isLoggedIn).toBe(true);
    expect(s.userId).toBe('uSysCode');
    expect(s.token).toBe('jwt');
    expect(s.displayName).toBe('Alice');
    expect(s.email).toBe('a@b.com');
    expect(s.netmindToken).toBe('nm-tok');
  });

  test('logout clears NetMind fields', () => {
    useConfigStore.getState().login('u', 'jwt', 'user', { displayName: 'A', email: 'a@b' });
    useConfigStore.getState().setNetmindToken('nm-tok');
    useConfigStore.getState().logout();
    const s = useConfigStore.getState();
    expect(s.netmindToken).toBe('');
    expect(s.displayName).toBe('');
    expect(s.email).toBe('');
  });

  test('login without profile keeps empty strings (back-compat for local mode)', () => {
    useConfigStore.getState().login('localuser');
    const s = useConfigStore.getState();
    expect(s.userId).toBe('localuser');
    expect(s.displayName).toBe('');
  });
});
