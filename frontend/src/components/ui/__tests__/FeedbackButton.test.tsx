/**
 * @file FeedbackButton.test.tsx
 * @description Floating feedback entry: opens the dialog on click, and sits
 * above the help "?" only when that corner slot is taken (chat view) —
 * otherwise it drops into the corner itself (sub-pages).
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { FeedbackButton } from '../FeedbackButton';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) => (typeof d === 'string' ? d : k),
  }),
}));

vi.mock('@/lib/api', () => ({
  api: { submitFeedback: vi.fn().mockResolvedValue({ ok: true, delivered: true }) },
}));

test('clicking the button opens the feedback dialog', () => {
  render(<FeedbackButton />);
  expect(screen.queryByRole('textbox')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'feedback.title' }));
  expect(screen.getByRole('textbox')).toBeTruthy();
});

test('stacks above the help button when the corner slot is taken', () => {
  render(<FeedbackButton aboveHelp />);
  const btn = screen.getByRole('button', { name: 'feedback.title' });
  expect(btn.className).toContain('bottom-14');
  expect(btn.className).not.toContain('bottom-4');
});

test('takes the corner slot itself when there is no help button', () => {
  render(<FeedbackButton />);
  const btn = screen.getByRole('button', { name: 'feedback.title' });
  expect(btn.className).toContain('bottom-4');
  expect(btn.className).not.toContain('bottom-14');
});
