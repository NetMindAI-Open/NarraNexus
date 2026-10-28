/**
 * @file_name: TeamBulletinPanel.test.tsx
 * @description: What the bulletin panel has to get right.
 *
 * Three properties, each protecting a decision made on the server:
 *
 *   1. The budget is VISIBLE before the user types. The server refuses an
 *      over-long rule instead of trimming it, which is only kind if the ceiling
 *      was on screen first — otherwise the user writes a rule and is told no.
 *   2. A server refusal is SHOWN, verbatim. The message names the limit and the
 *      number; swallowing it leaves the user guessing how much to cut.
 *   3. Agent-written rules are ATTRIBUTED and deletable. Agents can pin rules,
 *      and the user's ability to remove any of them is what makes that safe —
 *      which requires seeing at a glance which rules they did not write.
 *
 * The auto-summary is checked to render apart from the rules: presented
 * identically, best-effort machine output would read as authoritative as an
 * instruction the user typed.
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, v?: Record<string, unknown>) =>
      v ? `${k}(${Object.values(v).join(',')})` : k,
  }),
}));

import { TeamBulletinPanel } from '../TeamBulletinPanel';

const LIMITS = { max_entries: 20, max_entry_chars: 500, max_total_chars: 2000 };

function bulletin(entries: unknown[], usage = { entry_count: 0, total_chars: 0 }) {
  return { entries, usage, limits: LIMITS } as never;
}

function entry(over: Record<string, unknown> = {}) {
  return {
    entry_id: 'bul_1',
    team_id: 't1',
    content: 'always reply in English',
    source: 'user',
    author_id: 'usr_1',
    tier: 'long_term',
    ...over,
  };
}

const noop = async () => null;

function setup(props: Record<string, unknown> = {}) {
  const handlers = {
    onAdd: vi.fn(noop),
    onEdit: vi.fn(noop),
    onDelete: vi.fn(noop),
    onClearTier: vi.fn(noop),
  };
  render(
    <TeamBulletinPanel
      bulletin={bulletin([entry()], { entry_count: 1, total_chars: 22 })}
      loading={false}
      error={null}
      memberNames={{ agent_a: 'Alice' }}
      {...handlers}
      {...props}
    />,
  );
  return handlers;
}

describe('team bulletin panel', () => {
  beforeEach(() => vi.clearAllMocks());

  test('rules are listed', () => {
    setup();
    expect(screen.getByText('always reply in English')).toBeTruthy();
  });

  test('an empty bulletin says what the panel is for', () => {
    setup({ bulletin: bulletin([]) });
    expect(screen.getByTestId('bulletin-empty')).toBeTruthy();
  });

  // ── the budget is visible before you type ─────────────────────────────────

  test('usage against the ceiling is shown', () => {
    setup({ bulletin: bulletin([entry()], { entry_count: 3, total_chars: 120 }) });
    const usage = screen.getByTestId('bulletin-usage').textContent || '';
    expect(usage).toContain('3');
    expect(usage).toContain('20');
  });

  test('a full bulletin disables the input rather than letting the user write into a refusal', () => {
    setup({ bulletin: bulletin([entry()], { entry_count: 20, total_chars: 400 }) });
    expect((screen.getByTestId('bulletin-input') as HTMLTextAreaElement).disabled).toBe(true);
  });

  test('the character ceiling also disables it', () => {
    setup({ bulletin: bulletin([entry()], { entry_count: 2, total_chars: 2000 }) });
    expect((screen.getByTestId('bulletin-input') as HTMLTextAreaElement).disabled).toBe(true);
  });

  // ── refusals are shown verbatim ───────────────────────────────────────────

  test("a server refusal is displayed, not swallowed", async () => {
    const onAdd = vi.fn(async () => 'That entry is 900 characters; the limit is 500.');
    setup({ onAdd });

    fireEvent.change(screen.getByTestId('bulletin-input'), { target: { value: 'x' } });
    fireEvent.click(screen.getByTestId('bulletin-add'));

    await waitFor(() =>
      expect(screen.getByTestId('bulletin-refusal').textContent).toContain('limit is 500'),
    );
  });

  test('a refused entry keeps the draft so the user can shorten it', async () => {
    const onAdd = vi.fn(async () => 'too long');
    setup({ onAdd });

    fireEvent.change(screen.getByTestId('bulletin-input'), { target: { value: 'my rule' } });
    fireEvent.click(screen.getByTestId('bulletin-add'));

    await waitFor(() => expect(screen.getByTestId('bulletin-refusal')).toBeTruthy());
    expect((screen.getByTestId('bulletin-input') as HTMLTextAreaElement).value).toBe('my rule');
  });

  test('a successful add clears the draft', async () => {
    const { onAdd } = setup();

    fireEvent.change(screen.getByTestId('bulletin-input'), { target: { value: 'my rule' } });
    fireEvent.click(screen.getByTestId('bulletin-add'));

    await waitFor(() => expect(onAdd).toHaveBeenCalledWith('my rule', 'long_term'));
    await waitFor(() =>
      expect((screen.getByTestId('bulletin-input') as HTMLTextAreaElement).value).toBe(''),
    );
  });

  test('the chosen tier is passed through', async () => {
    const { onAdd } = setup();

    fireEvent.change(screen.getByTestId('bulletin-tier'), { target: { value: 'current_task' } });
    fireEvent.change(screen.getByTestId('bulletin-input'), { target: { value: 'this task' } });
    fireEvent.click(screen.getByTestId('bulletin-add'));

    await waitFor(() => expect(onAdd).toHaveBeenCalledWith('this task', 'current_task'));
  });

  test('a blank draft cannot be submitted', () => {
    setup();
    fireEvent.change(screen.getByTestId('bulletin-input'), { target: { value: '   ' } });
    expect((screen.getByTestId('bulletin-add') as HTMLButtonElement).disabled).toBe(true);
  });

  // ── agent rules are attributed and removable ──────────────────────────────

  test('an agent-written rule names its author', () => {
    setup({
      bulletin: bulletin([entry({ source: 'agent', author_id: 'agent_a' })]),
    });
    expect(screen.getByTestId('bulletin-author-bul_1').textContent).toContain('Alice');
  });

  test("a user's own rule carries no author label", () => {
    setup({ bulletin: bulletin([entry({ source: 'user', author_id: 'usr_1' })]) });
    expect(screen.queryByTestId('bulletin-author-bul_1')).toBeNull();
  });

  test('any rule can be deleted — the check that makes agent writes safe', async () => {
    const { onDelete } = setup({
      bulletin: bulletin([entry({ source: 'agent', author_id: 'agent_a' })]),
    });

    fireEvent.click(screen.getByTestId('bulletin-delete-bul_1'));

    await waitFor(() => expect(onDelete).toHaveBeenCalledWith('bul_1'));
  });

  // ── the summary stands apart ──────────────────────────────────────────────

  test('the auto-summary renders in its own labelled section', () => {
    setup({
      bulletin: bulletin([
        entry(),
        entry({ entry_id: 'bul_2', source: 'auto_summary', author_id: null, content: 'halfway' }),
      ]),
    });
    const summary = screen.getByTestId('bulletin-summary');
    expect(summary.textContent).toContain('halfway');
    expect(summary.textContent).toContain('progressHint');
  });

  test('the summary is not counted or listed as a rule', () => {
    setup({
      bulletin: bulletin([
        entry({ entry_id: 'bul_2', source: 'auto_summary', author_id: null, content: 'halfway' }),
      ]),
    });
    // No rules at all — so the empty state shows even though a summary exists.
    expect(screen.getByTestId('bulletin-empty')).toBeTruthy();
    expect(screen.queryByTestId('bulletin-delete-bul_2')).toBeNull();
  });

  // ── clearing one tier ─────────────────────────────────────────────────────

  test('the current-task tier can be cleared on its own', async () => {
    const { onClearTier } = setup({
      bulletin: bulletin([entry({ tier: 'current_task' })]),
    });

    fireEvent.click(screen.getByTestId('bulletin-clear-current-task'));

    await waitFor(() => expect(onClearTier).toHaveBeenCalledWith('current_task'));
  });

  test('there is no clear-all that could take the standing rules', () => {
    setup({ bulletin: bulletin([entry({ tier: 'long_term' })]) });
    // The standing rules are what the user least wants to retype, and retyping
    // is the thing this feature exists to prevent.
    expect(screen.queryByTestId('bulletin-clear-current-task')).toBeNull();
  });
});
