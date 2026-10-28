/**
 * @file_name: teamsStore.patrol.test.ts
 * @date: 2026-09-03
 * @description: The patrol switch's single copy — optimistic write, settle
 * window against in-flight polls, honest rollback.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const setTeamPatrol = vi.fn();
vi.mock('@/lib/api', () => ({
  api: { setTeamPatrol: (...a: unknown[]) => setTeamPatrol(...a) },
  ApiError: class extends Error {},
}));

import { PATROL_SETTLE_MS, useTeamsStore } from '../teamsStore';

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-09-03T00:00:00Z'));
  setTeamPatrol.mockReset();
  useTeamsStore.setState({ patrolByTeam: {}, patrolPendingUntil: {}, patrolInFlight: {} });
});
afterEach(() => vi.useRealTimers());

describe('teamsStore · patrol', () => {
  it('notePatrol records a reported value', () => {
    useTeamsStore.getState().notePatrol('t1', true);
    expect(useTeamsStore.getState().patrolByTeam).toEqual({ t1: true });
  });

  it('a poll landing inside the settle window cannot undo the optimistic write', async () => {
    useTeamsStore.getState().notePatrol('t1', true);
    setTeamPatrol.mockResolvedValue({ success: true });

    const write = useTeamsStore.getState().setPatrol('t1', false);
    // A GET that left before the click comes back with the pre-click value.
    useTeamsStore.getState().notePatrol('t1', true);
    expect(useTeamsStore.getState().patrolByTeam.t1).toBe(false);
    await write;
    useTeamsStore.getState().notePatrol('t1', true); // still within the window
    expect(useTeamsStore.getState().patrolByTeam.t1).toBe(false);

    // After the window a real flip from elsewhere must land.
    vi.advanceTimersByTime(PATROL_SETTLE_MS + 1);
    useTeamsStore.getState().notePatrol('t1', true);
    expect(useTeamsStore.getState().patrolByTeam.t1).toBe(true);
  });

  it('marks the write in flight only while the PUT is pending — never unbounded', async () => {
    let release: (v: unknown) => void = () => {};
    setTeamPatrol.mockReturnValue(new Promise((r) => { release = r; }));
    const write = useTeamsStore.getState().setPatrol('t1', false);
    expect(useTeamsStore.getState().patrolInFlight.t1).toBe(true);
    // A second click while in flight is dropped rather than raced.
    await useTeamsStore.getState().setPatrol('t1', true);
    expect(setTeamPatrol).toHaveBeenCalledTimes(1);
    release({ success: true });
    await write;
    expect(useTeamsStore.getState().patrolInFlight.t1).toBeUndefined();
    expect(Number.isFinite(useTeamsStore.getState().patrolPendingUntil.t1)).toBe(true);
  });

  it('a failed write rolls back to the reported value and closes the window', async () => {
    useTeamsStore.getState().notePatrol('t1', true);
    setTeamPatrol.mockRejectedValue(new Error('nope'));
    await expect(useTeamsStore.getState().setPatrol('t1', false)).rejects.toThrow('nope');
    expect(useTeamsStore.getState().patrolByTeam.t1).toBe(true);
    expect(useTeamsStore.getState().patrolPendingUntil.t1).toBeUndefined();
  });

  it('a failed write with nothing reported goes back to unknown, not to a guess', async () => {
    setTeamPatrol.mockRejectedValue(new Error('nope'));
    await expect(useTeamsStore.getState().setPatrol('t1', false)).rejects.toThrow('nope');
    expect('t1' in useTeamsStore.getState().patrolByTeam).toBe(false);
  });
});
