/**
 * Unit tests for the batch import runner (see lib/migrationImportQueue.ts).
 * The two contracts that matter to the user: one bad source never costs them
 * the rest of the batch, and "stop" never cuts an agent in half.
 */
import { describe, it, expect, vi } from 'vitest';
import {
  applyImportEdits,
  runImportQueue,
  summarizeBatch,
  type ImportQueueItem,
  type ImportQueueProgress,
} from '../migrationImportQueue';
import type { MigrationApplyResult, StandardizedAgentImport } from '@/types';

const scanOf = (name: string, sessionIds: string[] = []): StandardizedAgentImport => ({
  schema_version: '1',
  source: { framework: 'claude_code', detected_path: `/${name}`, detection_confidence: 'high' },
  agent: { name, system_prompt: '', description: '' },
  skills: [],
  memory: [],
  mcp_servers: [],
  sessions: sessionIds.map((id) => ({
    session_id: id,
    title: id,
    compact_text: '',
    turns: [],
    started_at: '',
  })),
  custom: { unmapped_files: [], credential_keys: [], llm_fallback_notes: '' },
});

const resultOf = (agentId: string): MigrationApplyResult => ({
  agent_id: agentId,
  created: true,
  awareness_written: true,
  memory_written: 0,
  default_skills_installed: [],
  skills_copied: ['a'],
  skills_installed: [],
  skills_unmatched: [],
  mcp_added: [],
  mcp_stdio_skipped: [],
  narratives_created: ['nar_1', 'nar_2'],
  memory_turns_retained: 7,
  warnings: [],
});

const item = (key: string, scanned?: StandardizedAgentImport): ImportQueueItem => ({
  key,
  path: `/${key}`,
  framework: 'claude_code',
  label: key,
  scanned: scanned ?? null,
});

describe('applyImportEdits', () => {
  it('renames the agent and keeps only the checked sessions', () => {
    const out = applyImportEdits(scanOf('old', ['s1', 's2']), '  new  ', new Set(['s2']));
    expect(out.agent.name).toBe('new');
    expect(out.sessions.map((s) => s.session_id)).toEqual(['s2']);
  });

  it('keeps every session when the row was never touched', () => {
    const out = applyImportEdits(scanOf('a', ['s1', 's2']));
    expect(out.sessions).toHaveLength(2);
    expect(out.agent.name).toBe('a');
  });

  it('falls back to the scanned name when the field was blanked', () => {
    expect(applyImportEdits(scanOf('a'), '   ').agent.name).toBe('a');
  });
});

describe('runImportQueue', () => {
  it('scans only the rows that were not pre-scanned, then applies in order', async () => {
    const scan = vi.fn(async (path: string) => scanOf(path.slice(1)));
    const apply = vi.fn(async (data: StandardizedAgentImport) => resultOf(data.agent.name));
    const events: string[] = [];

    const final = await runImportQueue([item('a', scanOf('a')), item('b')], {
      scan,
      apply,
      onProgress: (p) => events.push(`${p.key}:${p.status}`),
    });

    expect(scan).toHaveBeenCalledTimes(1); // only 'b'
    expect(apply.mock.calls.map((c) => c[0].agent.name)).toEqual(['a', 'b']);
    expect(events).toEqual(['a:importing', 'a:done', 'b:scanning', 'b:importing', 'b:done']);
    expect(final.map((p) => p.status)).toEqual(['done', 'done']);
  });

  it('keeps going after a row fails, and reports the error on that row', async () => {
    const apply = vi.fn(async (data: StandardizedAgentImport) => {
      if (data.agent.name === 'b') throw new Error('permission denied');
      return resultOf(data.agent.name);
    });

    const final = await runImportQueue(
      [item('a', scanOf('a')), item('b', scanOf('b')), item('c', scanOf('c'))],
      { scan: vi.fn(), apply, onProgress: () => {} },
    );

    expect(final.map((p) => p.status)).toEqual(['done', 'failed', 'done']);
    expect(final[1].error).toBe('permission denied');
    expect(apply).toHaveBeenCalledTimes(3);
  });

  it('stops between rows and never mid-write, marking the rest skipped', async () => {
    let stop = false;
    const apply = vi.fn(async (data: StandardizedAgentImport) => {
      if (data.agent.name === 'a') stop = true; // user hits "stop" during row a
      return resultOf(data.agent.name);
    });

    const final = await runImportQueue([item('a', scanOf('a')), item('b', scanOf('b')), item('c', scanOf('c'))], {
      scan: vi.fn(),
      apply,
      onProgress: () => {},
      shouldStop: () => stop,
    });

    expect(final.map((p) => p.status)).toEqual(['done', 'skipped', 'skipped']);
    expect(apply).toHaveBeenCalledTimes(1); // row a completed, nothing was cut off
  });

  it('reports a scan failure without ever calling apply', async () => {
    const apply = vi.fn();
    const final = await runImportQueue([item('a')], {
      scan: vi.fn(async () => {
        throw new Error('no such folder');
      }),
      apply,
      onProgress: () => {},
    });
    expect(final[0]).toMatchObject({ status: 'failed', error: 'no such folder' });
    expect(apply).not.toHaveBeenCalled();
  });
});

describe('summarizeBatch', () => {
  it('totals the dimensions across the successful rows only', () => {
    const rows: ImportQueueProgress[] = [
      { key: 'a', label: 'a', status: 'done', result: resultOf('a') },
      { key: 'b', label: 'b', status: 'failed', error: 'x' },
      { key: 'c', label: 'c', status: 'done', result: resultOf('c') },
      { key: 'd', label: 'd', status: 'skipped' },
    ];
    expect(summarizeBatch(rows)).toMatchObject({
      imported: 2,
      failed: 1,
      skipped: 1,
      narratives: 4,
      memoryTurns: 14,
      skills: 2,
    });
  });
});
