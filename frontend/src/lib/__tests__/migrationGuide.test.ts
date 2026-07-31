/**
 * Unit tests for the migration guided-flow persistence — the "only shows once
 * per user" correctness contract (see lib/migrationGuide.ts).
 */
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { readMigrationGuide, writeMigrationGuide } from '../migrationGuide';

beforeEach(() => {
  localStorage.clear();
});

describe('migrationGuide persistence', () => {
  it('defaults to all-false when nothing is stored', () => {
    expect(readMigrationGuide('u1')).toEqual({
      welcomed: false,
      coachmarkPending: false,
      coachmarkDone: false,
    });
  });

  it('merges a patch and reads it back (welcomed once, never again)', () => {
    const after = writeMigrationGuide('u1', { welcomed: true, coachmarkPending: true });
    expect(after.welcomed).toBe(true);
    expect(after.coachmarkPending).toBe(true);
    expect(after.coachmarkDone).toBe(false);
    // a later patch merges, not replaces
    writeMigrationGuide('u1', { coachmarkDone: true });
    expect(readMigrationGuide('u1')).toEqual({
      welcomed: true,
      coachmarkPending: true,
      coachmarkDone: true,
    });
  });

  it('is isolated per user', () => {
    writeMigrationGuide('u1', { welcomed: true });
    expect(readMigrationGuide('u2').welcomed).toBe(false); // u2 unaffected
    expect(readMigrationGuide('u1').welcomed).toBe(true);
  });

  it('falls back to defaults on corrupt JSON', () => {
    localStorage.setItem('nn_migration_guide:u1', '{not json');
    expect(readMigrationGuide('u1').welcomed).toBe(false);
  });

  it('degrades quietly when storage throws', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    // returns the merged state even though it could not persist
    expect(writeMigrationGuide('u1', { welcomed: true }).welcomed).toBe(true);
    spy.mockRestore();
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});
