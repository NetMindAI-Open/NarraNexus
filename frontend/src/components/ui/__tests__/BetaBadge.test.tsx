/**
 * @file BetaBadge.test.tsx
 * @description Brand beta marker: renders the untranslated "Beta" wordmark
 * label and exposes the translated expectation-setting note as its
 * accessible description (tooltip).
 */
import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { BetaBadge } from '../BetaBadge';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) => (typeof d === 'string' ? d : k),
  }),
}));

test('renders the literal Beta label (brand lockup, never translated)', () => {
  render(<BetaBadge />);
  expect(screen.getByText('Beta')).toBeTruthy();
});

test('carries the translated tooltip note for expectation management', () => {
  render(<BetaBadge />);
  // Radix keeps tooltip content unmounted until hover; the trigger still
  // must expose the note non-interactively via aria-label.
  expect(screen.getByLabelText('common.betaTooltip')).toBeTruthy();
});
