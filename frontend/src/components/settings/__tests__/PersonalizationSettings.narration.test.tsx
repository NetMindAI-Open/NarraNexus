/**
 * The progress-narration preference is REACHABLE in Settings.
 *
 * This test exists for a specific reason: the preference was implemented and
 * documented but had no UI entry point, which means "a preference that exists
 * in the docs and not in the product" — and the two "with the preference off"
 * cases in TurnTimeline.narrationTier would then be asserting a state no code
 * path can reach. This pins the entry point itself.
 */
import { describe, expect, test, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { PersonalizationSettings } from '../PersonalizationSettings';
import { useUIStore } from '@/stores/uiStore';

const toggle = () => screen.getByRole('checkbox');

describe('PersonalizationSettings — progress narration', () => {
  beforeEach(() => {
    useUIStore.setState({ interimNarration: true });
  });

  test('defaults on, and the control reflects the current state', () => {
    render(<PersonalizationSettings />);
    expect(toggle().getAttribute('aria-checked')).toBe('true');
  });

  test('one click turns it off; store and control stay in sync', () => {
    render(<PersonalizationSettings />);
    fireEvent.click(toggle());

    expect(useUIStore.getState().interimNarration).toBe(false);
    expect(toggle().getAttribute('aria-checked')).toBe('false');
  });

  test('clicking again turns it back on', () => {
    useUIStore.setState({ interimNarration: false });
    render(<PersonalizationSettings />);
    fireEvent.click(toggle());

    expect(useUIStore.getState().interimNarration).toBe(true);
  });

  test('the preference persists to localStorage (off = 0)', () => {
    render(<PersonalizationSettings />);
    fireEvent.click(toggle());

    expect(window.localStorage.getItem('interim_narration_v1')).toBe('0');
  });

  test('the accessible name is the heading, not the whole hint sentence', () => {
    render(<PersonalizationSettings />);
    const labelledBy = toggle().getAttribute('aria-labelledby');

    expect(labelledBy).toBe('narration-label');
    expect(document.getElementById(labelledBy!)?.textContent).toBe('Progress narration');
    expect(toggle().getAttribute('aria-describedby')).toBe('narration-hint');
  });
});
