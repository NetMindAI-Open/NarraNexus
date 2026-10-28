/**
 * @file_name: ComposerFastToggle.test.tsx
 * @description: Behavior contract for the composer fast-mode switch.
 *
 * Locks: aria-pressed mirrors the enabled state; clicking reports the
 * inverted value; disabled blocks toggling.
 */
import { describe, expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { ComposerFastToggle } from '../ComposerFastToggle';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

describe('ComposerFastToggle', () => {
  test('reflects state via aria-pressed and calls onToggle with inverse', () => {
    const onToggle = vi.fn();
    render(<ComposerFastToggle enabled={false} onToggle={onToggle} />);
    const btn = screen.getByRole('button');
    expect(btn).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(btn);
    expect(onToggle).toHaveBeenCalledWith(true);
  });

  test('enabled state reports aria-pressed true and toggles off', () => {
    const onToggle = vi.fn();
    render(<ComposerFastToggle enabled onToggle={onToggle} />);
    const btn = screen.getByRole('button');
    expect(btn).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(btn);
    expect(onToggle).toHaveBeenCalledWith(false);
  });

  test('disabled blocks toggling', () => {
    const onToggle = vi.fn();
    render(<ComposerFastToggle enabled={false} onToggle={onToggle} disabled />);
    fireEvent.click(screen.getByRole('button'));
    expect(onToggle).not.toHaveBeenCalled();
  });
});
