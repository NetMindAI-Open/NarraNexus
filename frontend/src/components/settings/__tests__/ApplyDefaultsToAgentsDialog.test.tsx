import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { ApplyDefaultsToAgentsDialog } from '../ApplyDefaultsToAgentsDialog';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, f?: unknown) => (typeof f === 'string' ? f : k) }),
}));

afterEach(() => vi.restoreAllMocks());

const STATS = { agent: 12, helper_llm: 0, total_agents: 15 };

test('agent slot checkable, helper slot disabled when zero overrides', () => {
  render(<ApplyDefaultsToAgentsDialog isOpen stats={STATS} dirtySlots={['agent', 'helper_llm']} onClose={() => {}} onApply={vi.fn()} />);
  const agentCb = screen.getByTestId('apply-slot-agent') as HTMLInputElement;
  const helperCb = screen.getByTestId('apply-slot-helper_llm') as HTMLInputElement;
  expect(agentCb.disabled).toBe(false);
  expect(helperCb.disabled).toBe(true);
});

test('apply calls onApply with only checked slots', async () => {
  const onApply = vi.fn().mockResolvedValue(undefined);
  render(<ApplyDefaultsToAgentsDialog isOpen stats={STATS} dirtySlots={['agent', 'helper_llm']} onClose={() => {}} onApply={onApply} />);
  fireEvent.click(screen.getByTestId('apply-slot-agent')); // check agent
  fireEvent.click(screen.getByTestId('apply-confirm-btn'));
  await waitFor(() => expect(onApply).toHaveBeenCalledWith(['agent']));
});

test('save-only button closes without applying', () => {
  const onClose = vi.fn();
  const onApply = vi.fn();
  render(<ApplyDefaultsToAgentsDialog isOpen stats={STATS} dirtySlots={['agent', 'helper_llm']} onClose={onClose} onApply={onApply} />);
  fireEvent.click(screen.getByTestId('apply-cancel-btn'));
  expect(onApply).not.toHaveBeenCalled();
  expect(onClose).toHaveBeenCalled();
});

test('only dirty slots are offered — a helper-only change never shows the agent slot', () => {
  render(<ApplyDefaultsToAgentsDialog isOpen stats={{ agent: 12, helper_llm: 4, total_agents: 15 }} dirtySlots={['helper_llm']} onClose={() => {}} onApply={vi.fn()} />);
  expect(screen.queryByTestId('apply-slot-agent')).not.toBeInTheDocument();
  expect(screen.getByTestId('apply-slot-helper_llm')).toBeInTheDocument();
});

test('confirm disabled until a slot is checked', () => {
  render(<ApplyDefaultsToAgentsDialog isOpen stats={STATS} dirtySlots={['agent', 'helper_llm']} onClose={() => {}} onApply={vi.fn()} />);
  const btn = screen.getByTestId('apply-confirm-btn') as HTMLButtonElement;
  expect(btn.disabled).toBe(true);
  fireEvent.click(screen.getByTestId('apply-slot-agent'));
  expect(btn.disabled).toBe(false);
});
