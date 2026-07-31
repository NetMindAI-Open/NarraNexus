/**
 * @file_name: drawerPinToggle.test.tsx
 * @date: 2026-07-30
 * @description: Pinning must not remount the panel.
 *
 * Regression: MainLayout used to render the two drawer modes as two SEPARATE
 * <BookmarkDrawer> elements — one inline in the flex row for pinned, one in a
 * `!drawerPinned &&` branch for the slide-over. Different positions in the
 * React tree means toggling the pin unmounted one and mounted the other, so the
 * panel inside was rebuilt from scratch every time and every bit of local state
 * the user had set up (job status filter, view mode, expanded rows) silently
 * reset to defaults. Reported as "点击/取消 然后页面上的交互感觉怪怪的".
 *
 * The fix relies on a property of portals that is easy to lose in a later
 * refactor: `createPortal` changes the DOM parent but NOT the React tree
 * position, so ONE element switching between portal and inline keeps its
 * subtree mounted. These tests fail the moment someone splits it back apart.
 */

import { describe, it, expect, vi } from 'vitest';
import { useState } from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { BookmarkDrawer } from '../BookmarkDrawer';

/**
 * Stands in for a real panel: holds local state the user set up, and reports
 * every mount so a remount can't hide.
 */
function StatefulPanel({ onMount }: { onMount: () => void }) {
  useState(() => {
    onMount();
    return null;
  });
  const [filter, setFilter] = useState('all');
  return (
    <div>
      <button onClick={() => setFilter('running')}>set filter</button>
      <span data-testid="filter">{filter}</span>
    </div>
  );
}

/** The caller shape MainLayout uses: one drawer element, pinned is state. */
function Host({ onMount, initialPinned = false }: { onMount: () => void; initialPinned?: boolean }) {
  const [pinned, setPinned] = useState(initialPinned);
  return (
    <BookmarkDrawer
      open
      pinned={pinned}
      onPinnedChange={setPinned}
      onClose={vi.fn()}
      title="Jobs"
      edgeReservePx={76}
    >
      <StatefulPanel onMount={onMount} />
    </BookmarkDrawer>
  );
}

describe('BookmarkDrawer — pin toggle preserves the panel', () => {
  it('mounts the panel exactly once across unpinned → pinned → unpinned', () => {
    const onMount = vi.fn();
    render(<Host onMount={onMount} />);
    expect(onMount).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: 'Pin panel' }));
    expect(onMount).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: 'Unpin panel' }));
    expect(onMount).toHaveBeenCalledTimes(1);
  });

  it("keeps the user's in-panel state when pinning", () => {
    render(<Host onMount={vi.fn()} />);

    // The user sets something up inside the panel...
    fireEvent.click(screen.getByRole('button', { name: 'set filter' }));
    expect(screen.getByTestId('filter')).toHaveTextContent('running');

    // ...then pins. Their choice must survive the mode switch.
    fireEvent.click(screen.getByRole('button', { name: 'Pin panel' }));
    expect(screen.getByTestId('filter')).toHaveTextContent('running');

    fireEvent.click(screen.getByRole('button', { name: 'Unpin panel' }));
    expect(screen.getByTestId('filter')).toHaveTextContent('running');
  });

  it('actually switches presentation: portal + backdrop only when unpinned', () => {
    render(<Host onMount={vi.fn()} />);

    // Unpinned: overlay in a portal on body, with a click-catching backdrop.
    expect(document.body.querySelector('[data-drawer-backdrop]')).not.toBeNull();
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Pin panel' }));

    // Pinned: a plain column — no backdrop, not a dialog.
    expect(document.body.querySelector('[data-drawer-backdrop]')).toBeNull();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('gives the pinned column the width it was handed', () => {
    render(
      <BookmarkDrawer
        open
        pinned
        onPinnedChange={vi.fn()}
        onClose={vi.fn()}
        title="Jobs"
        pinnedWidth={520}
      >
        <div>content</div>
      </BookmarkDrawer>,
    );
    const column = screen.getByText('content').closest('div[style*="width"]') as HTMLElement;
    expect(column.style.width).toBe('520px');
  });
});

describe('BookmarkDrawer — header controls explain themselves on hover', () => {
  it('every header button has a title, not just an aria-label', () => {
    render(<Host onMount={vi.fn()} />);
    // Regression: these carried aria-label only, so a mouse user hovering the
    // pin got no explanation at all — which is how the Owner ended up asking
    // what the button was for.
    expect(screen.getByRole('button', { name: 'Pin panel' })).toHaveAttribute(
      'title',
      'Pin panel',
    );
    expect(screen.getByRole('button', { name: 'Close panel' })).toHaveAttribute(
      'title',
      'Close panel',
    );

    fireEvent.click(screen.getByRole('button', { name: 'Pin panel' }));
    expect(screen.getByRole('button', { name: 'Unpin panel' })).toHaveAttribute(
      'title',
      'Unpin panel',
    );
  });
});
