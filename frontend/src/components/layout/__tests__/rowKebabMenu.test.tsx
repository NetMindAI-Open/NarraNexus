/**
 * RowKebabMenu is the one dropdown shell both sidebar row menus (agent + team)
 * render. These tests pin the shell contract the hosts rely on: onOpenChange
 * on open AND close (the host row lifts its z-index with it), item clicks
 * never reach the row, a disabled item does nothing, Escape dismisses.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { RowKebabMenu } from '../RowKebabMenu';

function setup() {
  const onRow = vi.fn();
  const onOpenChange = vi.fn();
  const onGo = vi.fn();
  const onOff = vi.fn();
  render(
    <div onClick={onRow}>
      <RowKebabMenu
        ariaLabel="Row options"
        onOpenChange={onOpenChange}
        items={[
          { key: 'go', icon: null, label: 'Go', onSelect: onGo },
          { key: 'off', icon: null, label: 'Off', disabled: true, onSelect: onOff },
        ]}
      />
    </div>,
  );
  return { onRow, onOpenChange, onGo, onOff };
}

describe('RowKebabMenu', () => {
  it('reports open and close to the host and keeps clicks off the row', () => {
    const { onRow, onOpenChange, onGo } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Row options' }));
    expect(onOpenChange).toHaveBeenLastCalledWith(true);
    expect(screen.getByRole('button', { name: 'Row options' })).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Go' }));
    expect(onGo).toHaveBeenCalledTimes(1);
    expect(onOpenChange).toHaveBeenLastCalledWith(false);
    expect(screen.queryByRole('button', { name: 'Go' })).not.toBeInTheDocument();
    expect(onRow).not.toHaveBeenCalled();
  });

  it('a disabled item does not fire and leaves the menu open', () => {
    const { onOff } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Row options' }));
    fireEvent.click(screen.getByRole('button', { name: 'Off' }));
    expect(onOff).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Go' })).toBeInTheDocument();
  });

  it('Escape closes the panel and tells the host', () => {
    const { onOpenChange } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Row options' }));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('button', { name: 'Go' })).not.toBeInTheDocument();
    expect(onOpenChange).toHaveBeenLastCalledWith(false);
  });
});
