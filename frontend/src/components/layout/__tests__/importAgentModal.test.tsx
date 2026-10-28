/**
 * Tests for the one-page ImportAgentModal — the multi-select contract the old
 * four-stage wizard could not express: several rows checked at once, imported
 * in one pass, and a single failing row not costing the user the others.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import { ImportAgentModal } from '../ImportAgentModal';
import type {
  FrameworkDetection,
  MigrationApplyResult,
  StandardizedAgentImport,
} from '@/types';

vi.mock('@/lib/api', () => ({
  api: {
    migrateDetect: vi.fn(),
    migrateScan: vi.fn(),
    migrateApply: vi.fn(),
    migrateHurry: vi.fn(),
  },
}));

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const api = (await import('@/lib/api')).api as any;

const detections: FrameworkDetection[] = [
  { framework: 'claude_code', path: '/w/big', confidence: 'high', signals: ['project', 'sessions:12'] },
  { framework: 'claude_code', path: '/w/empty', confidence: 'high', signals: ['project', 'sessions:0'] },
  { framework: 'openclaw', path: '/w/claw', confidence: 'high', signals: ['sessions:2'] },
];

const scanOf = (path: string): StandardizedAgentImport => ({
  schema_version: '1',
  source: { framework: 'claude_code', detected_path: path, detection_confidence: 'high' },
  agent: { name: path.split('/').pop()!, system_prompt: '', description: '' },
  skills: [],
  memory: [],
  mcp_servers: [],
  sessions: [],
  custom: { unmapped_files: [], credential_keys: [], llm_fallback_notes: '' },
});

const resultOf = (name: string): MigrationApplyResult => ({
  agent_id: `agt_${name}`,
  created: true,
  awareness_written: true,
  memory_written: 1,
  default_skills_installed: [],
  skills_copied: [],
  skills_installed: [],
  skills_unmatched: [],
  mcp_added: [],
  mcp_stdio_skipped: [],
  narratives_created: [],
  memory_turns_retained: 0,
  warnings: [],
});

const renderModal = (onApplied = vi.fn(), onClose = vi.fn()) => {
  render(
    <ImportAgentModal
      initialDetections={detections}
      onApplied={onApplied}
      onClose={onClose}
    />,
  );
  return { onApplied, onClose };
};

beforeEach(() => {
  vi.clearAllMocks();
  api.migrateScan.mockImplementation(async (path: string) => scanOf(path));
  api.migrateApply.mockImplementation(async (data: StandardizedAgentImport) =>
    resultOf(data.agent.name),
  );
  api.migrateHurry.mockResolvedValue({ success: true });
});

/** Multi-row groups render closed; open one by clicking its header strip. */
const expandGroup = (label: RegExp) =>
  fireEvent.click(screen.getByRole('button', { expanded: false, name: label }));

describe('ImportAgentModal — list phase', () => {
  it('reuses the caller detections instead of re-scanning', () => {
    renderModal();
    expect(api.migrateDetect).not.toHaveBeenCalled();
    // one-row tools are always visible; the 2-row Claude Code group is a header
    expect(screen.getByText('OpenClaw')).toBeInTheDocument();
    expect(screen.getByText('Claude Code')).toBeInTheDocument();
  });

  it('keeps a multi-row group closed until asked, and says how many are checked', () => {
    renderModal();
    // closed: the projects are not in the DOM, the header carries the counts
    expect(screen.queryByText('big')).not.toBeInTheDocument();
    expect(screen.getByText(/1 of 2 checked/i)).toBeInTheDocument();

    expandGroup(/claude code/i);
    expect(screen.getByText('big')).toBeInTheDocument();
    expect(screen.getByText('empty')).toBeInTheDocument();
  });

  it('pre-checks only the rows carrying sessions', () => {
    renderModal();
    expandGroup(/claude code/i);
    const box = (name: string) => screen.getByRole('checkbox', { name });
    expect(box('big')).toHaveAttribute('aria-checked', 'true');
    expect(box('OpenClaw')).toHaveAttribute('aria-checked', 'true');
    expect(box('empty')).toHaveAttribute('aria-checked', 'false');
    // footer echoes the pre-selection (2 rows, 12 + 2 sessions) — it counts the
    // whole list, not just what is currently visible
    expect(screen.getByText(/2 selected · 14 sessions/i)).toBeInTheDocument();
  });

  it('imports exactly the checked rows and reports the batch', async () => {
    const { onApplied } = renderModal();
    fireEvent.click(screen.getByRole('button', { name: /^import 2$/i }));

    await waitFor(() => expect(screen.getByText(/agents imported/i)).toBeInTheDocument());
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(api.migrateApply).toHaveBeenCalledTimes(2);
    expect(api.migrateApply.mock.calls.map((c: [StandardizedAgentImport]) => c[0].agent.name)).toEqual([
      'big',
      'claw',
    ]);

    fireEvent.click(screen.getByRole('button', { name: /open big/i }));
    expect(onApplied).toHaveBeenCalledWith(
      [resultOf('big'), resultOf('claw')],
      { open: true },
    );
  });

  it('still refreshes without navigating when the batch is closed', async () => {
    const { onApplied } = renderModal();
    fireEvent.click(screen.getByRole('button', { name: /^import 2$/i }));
    await waitFor(() => expect(screen.getByText(/agents imported/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /^close$/i }));
    expect(onApplied).toHaveBeenCalledWith(expect.any(Array), { open: false });
  });

  it('stopping tells the server to hurry the row that is already writing', async () => {
    // Owner objection 2026-09-03: "stop" must not mean waiting out the current
    // project. The queue can't abort an in-flight write (that would leave a
    // half-populated agent), so it asks the server to finish without LLM
    // summaries — and skips every row that hasn't started.
    const appliedImportIds: string[] = [];
    let release!: (r: MigrationApplyResult) => void;
    api.migrateApply.mockImplementation(
      (data: StandardizedAgentImport, _agentId: unknown, importId: string) =>
        new Promise<MigrationApplyResult>((resolve) => {
          release = () => resolve({ ...resultOf(data.agent.name), summaries_degraded: 4 });
          appliedImportIds.push(importId);
        }),
    );

    renderModal();
    fireEvent.click(screen.getByRole('button', { name: /^import 2$/i }));
    await waitFor(() => expect(appliedImportIds.length).toBe(1));

    fireEvent.click(screen.getByRole('button', { name: /^stop$/i }));
    // the hurry targets the apply that is running, by its own handle
    expect(api.migrateHurry).toHaveBeenCalledWith(appliedImportIds[0]);

    release(resultOf('big'));
    await waitFor(() => expect(screen.getByText(/agent imported/i)).toBeInTheDocument());
    // second row never started, and the degraded summaries are stated, not hidden
    expect(api.migrateApply).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/1 not started/i)).toBeInTheDocument();
    expect(screen.getByText(/plain summaries/i)).toBeInTheDocument();
  });

  it('keeps importing after one row fails, and offers a retry for it', async () => {
    api.migrateApply.mockImplementation(async (data: StandardizedAgentImport) => {
      if (data.agent.name === 'big') throw new Error('permission denied');
      return resultOf(data.agent.name);
    });

    renderModal();
    fireEvent.click(screen.getByRole('button', { name: /^import 2$/i }));

    await waitFor(() => expect(screen.getByText(/agent imported/i)).toBeInTheDocument());
    expect(screen.getByText(/1 failed/i)).toBeInTheDocument();
    expect(screen.getByText(/permission denied/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
    expect(api.migrateApply).toHaveBeenCalledTimes(2);
  });

  it('scans a row lazily when the row is opened, not before', async () => {
    renderModal();
    expandGroup(/claude code/i);
    expect(api.migrateScan).not.toHaveBeenCalled();

    // Clicking the row body OPENS it — it must not change the selection.
    fireEvent.click(screen.getByRole('button', { name: /big/i }));
    await waitFor(() => expect(api.migrateScan).toHaveBeenCalledWith('/w/big', 'claude_code'));
    await waitFor(() => expect(screen.getByDisplayValue('big')).toBeInTheDocument());
    expect(screen.getByRole('checkbox', { name: 'big' })).toHaveAttribute('aria-checked', 'true');
  });

  it('only the checkbox changes the selection', () => {
    renderModal();
    expandGroup(/claude code/i);
    const box = screen.getByRole('checkbox', { name: 'empty' });
    expect(box).toHaveAttribute('aria-checked', 'false');

    // row body: opens, selection untouched
    fireEvent.click(screen.getByRole('button', { name: /empty/i }));
    expect(box).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByText(/2 selected · 14 sessions/i)).toBeInTheDocument();

    // checkbox: selects
    fireEvent.click(box);
    expect(box).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByText(/3 selected · 14 sessions/i)).toBeInTheDocument();
  });
});
