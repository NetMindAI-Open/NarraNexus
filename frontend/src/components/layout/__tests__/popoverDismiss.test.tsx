/**
 * Row-menu popovers must dismiss on interaction anywhere on the page, not
 * only inside the sidebar. A fixed-position backdrop cannot guarantee that:
 * rendered inside an animated row (which keeps a transform), it is laid out
 * against the row instead of the viewport. These tests pin the
 * document-level behaviour that replaced it.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { TeamRowMenu } from '../TeamRowMenu';

const teamMenuProps = {
  onAddAgent: vi.fn(),
  addingAgent: false,
  onRename: vi.fn(),
  onDelete: vi.fn(),
};

describe('TeamRowMenu dismissal', () => {
  it('closes on pointerdown anywhere outside the menu', () => {
    render(
      <div>
        <TeamRowMenu {...teamMenuProps} />
        <button data-testid="elsewhere">elsewhere</button>
      </div>,
    );
    fireEvent.click(screen.getByRole('button', { name: /options/i }));
    expect(screen.getByText(/rename/i)).toBeInTheDocument();
    fireEvent.pointerDown(screen.getByTestId('elsewhere'));
    expect(screen.queryByText(/rename/i)).not.toBeInTheDocument();
  });
});
