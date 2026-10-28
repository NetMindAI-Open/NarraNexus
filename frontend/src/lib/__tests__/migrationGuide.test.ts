/**
 * Unit tests for the import coach-mark's persistence (see lib/migrationGuide.ts).
 * The contract: armed when the user skips the first-run import step, per user,
 * and gone for good once dismissed — across reloads.
 */
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { readMigrationGuide, writeMigrationGuide } from '../migrationGuide';

beforeEach(() => {
  localStorage.clear();
});

describe('migrationGuide persistence', () => {
  it('defaults to all-false when nothing is stored', () => {
    expect(readMigrationGuide('u1')).toEqual({
      coachmarkPending: false,
      coachmarkDone: false,
    });
  });

  it('arms on skip, then stays dismissed once clicked away', () => {
    const armed = writeMigrationGuide('u1', { coachmarkPending: true });
    expect(armed.coachmarkPending).toBe(true);
    expect(armed.coachmarkDone).toBe(false);
    // a later patch merges, not replaces — the bubble can't re-arm itself
    writeMigrationGuide('u1', { coachmarkDone: true });
    expect(readMigrationGuide('u1')).toEqual({
      coachmarkPending: true,
      coachmarkDone: true,
    });
  });

  it('is isolated per user', () => {
    writeMigrationGuide('u1', { coachmarkPending: true });
    expect(readMigrationGuide('u2').coachmarkPending).toBe(false); // u2 unaffected
    expect(readMigrationGuide('u1').coachmarkPending).toBe(true);
  });

  it('falls back to defaults on corrupt JSON', () => {
    localStorage.setItem('nn_migration_guide:u1', '{not json');
    expect(readMigrationGuide('u1').coachmarkPending).toBe(false);
  });

  it('degrades quietly when storage throws', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    // returns the merged state even though it could not persist
    expect(writeMigrationGuide('u1', { coachmarkPending: true }).coachmarkPending).toBe(true);
    spy.mockRestore();
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});
