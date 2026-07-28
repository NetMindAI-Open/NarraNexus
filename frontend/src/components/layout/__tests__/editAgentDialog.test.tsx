/**
 * Tests for EditAgentDialog — the name/description editor with the 255-char
 * ceiling. Covers the counter, the over-limit block (Save disabled + error),
 * and a successful save. Regression guard for #71's UX half: the user must be
 * stopped at 255 in the UI, not by the server's 422.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { EditAgentDialog } from '../EditAgentDialog';
import { AGENT_TEXT_MAX_LENGTH } from '@/lib/agentLimits';

const base = {
  initialName: 'My Agent',
  initialDescription: 'short',
  onCancel: vi.fn(),
  onSave: vi.fn(),
};

const saveButton = () => screen.getByRole('button', { name: /save|保存/i });

describe('EditAgentDialog', () => {
  it('shows a live counter for the description', () => {
    render(<EditAgentDialog {...base} initialDescription="hello" />);
    expect(screen.getByText(`5/${AGENT_TEXT_MAX_LENGTH}`)).toBeInTheDocument();
  });

  it('disables Save and shows an error when description exceeds the limit', () => {
    render(
      <EditAgentDialog {...base} initialDescription={'x'.repeat(AGENT_TEXT_MAX_LENGTH + 1)} />
    );
    expect(saveButton()).toBeDisabled();
    // counter reflects the over-limit length
    expect(
      screen.getByText(`${AGENT_TEXT_MAX_LENGTH + 1}/${AGENT_TEXT_MAX_LENGTH}`)
    ).toBeInTheDocument();
  });

  it('disables Save when the name exceeds the limit', () => {
    render(<EditAgentDialog {...base} initialName={'n'.repeat(AGENT_TEXT_MAX_LENGTH + 1)} />);
    expect(saveButton()).toBeDisabled();
  });

  it('disables Save when the name is empty', () => {
    render(<EditAgentDialog {...base} initialName="   " />);
    expect(saveButton()).toBeDisabled();
  });

  it('saves the trimmed name and description when within the limit', () => {
    const onSave = vi.fn();
    render(<EditAgentDialog {...base} onSave={onSave} initialName="  Fixed  " initialDescription="A good description" />);
    fireEvent.click(saveButton());
    expect(onSave).toHaveBeenCalledWith('Fixed', 'A good description');
  });
});
