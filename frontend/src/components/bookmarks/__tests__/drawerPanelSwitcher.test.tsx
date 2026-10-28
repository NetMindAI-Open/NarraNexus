/**
 * @file_name: drawerPanelSwitcher.test.tsx
 * @date: 2026-09-11
 * @description: The drawer header's title is a panel switcher.
 *
 * Owner-required (reinstated 2026-09-11 after #383 removed it): a pinned
 * drawer is an independent window and owns its own content controls —
 * switching what it shows must not require a trip back to the chat header
 * or the team bar. The single-chat menu is fed by the tabs registry
 * (`visibleCategories`), the team menu by `teamDrawerCategories`, so every
 * panel is reachable from the drawer itself.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';

import { BookmarkDrawer, allTabs, stripCategories, visibleCategories, type DrawerSwitcherCategory } from '..';
import { teamDrawerCategories } from '@/components/chat/team/teamTabs';

const STUDIO_OPEN = { studioOpen: true, studioResumable: false };
const NO_STUDIO = { studioOpen: false, studioResumable: false };

function renderDrawer(
  onSelectTab = vi.fn(),
  categories: ReadonlyArray<DrawerSwitcherCategory> = visibleCategories(STUDIO_OPEN),
  activeTab: string | null = 'artifacts',
) {
  render(
    <BookmarkDrawer
      open
      pinned
      onPinnedChange={vi.fn()}
      onClose={vi.fn()}
      title="ARTIFACTS"
      activeTab={activeTab}
      onSelectTab={onSelectTab}
      switcherCategories={categories}
    >
      <div>panel body</div>
    </BookmarkDrawer>,
  );
  return onSelectTab;
}

const openMenu = () => fireEvent.click(screen.getByRole('button', { name: /switch panel/i }));
const items = () => screen.queryAllByRole('menuitemradio');
const itemIds = () => items().map((el) => el.getAttribute('data-testid')!.replace('drawer-switcher-item-', ''));

describe('drawer panel switcher — single chat', () => {
  it('the title is a menu button that opens a dropdown of every registered panel', () => {
    renderDrawer();
    const trigger = screen.getByRole('button', { name: /switch panel/i });
    expect(trigger.getAttribute('aria-haspopup')).toBe('menu');
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByRole('menu')).toBeNull();

    openMenu();
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByRole('menu')).toBeTruthy();
    // Every panel in the registry has exactly one row, in registry order.
    expect(allTabs().length).toBeGreaterThan(5);
    expect(itemIds()).toEqual(allTabs().map((t) => t.id));
  });

  it("the trigger's accessible name carries the open panel's title plus the action", () => {
    // aria-label overrides the button's text, so the title must be in it —
    // in pinned mode nothing else in the drawer announces which panel is open.
    renderDrawer();
    const trigger = screen.getByRole('button', { name: /switch panel/i });
    expect(trigger.getAttribute('aria-label')).toMatch(/^ARTIFACTS · /);
    expect(screen.getByRole('button', { name: /ARTIFACTS/ })).toBe(trigger);
  });

  it('groups rows under their strip categories', () => {
    renderDrawer();
    openMenu();
    const groups = within(screen.getByRole('menu')).getAllByRole('group');
    const cats = stripCategories();
    expect(groups.length).toBe(cats.length);
    groups.forEach((g, i) => {
      expect(within(g).getAllByRole('menuitemradio').length).toBe(cats[i].tabs.length);
    });
  });

  it('marks the open panel as checked and only that one', () => {
    renderDrawer();
    openMenu();
    const checked = items().filter((el) => el.getAttribute('aria-checked') === 'true');
    expect(checked.map((el) => el.getAttribute('data-testid'))).toEqual(['drawer-switcher-item-artifacts']);
  });

  it('selecting another panel fires onSelectTab with its id and closes the menu', () => {
    const onSelectTab = renderDrawer();
    openMenu();
    fireEvent.click(screen.getByTestId('drawer-switcher-item-jobs'));
    expect(onSelectTab).toHaveBeenCalledTimes(1);
    expect(onSelectTab).toHaveBeenCalledWith('jobs');
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('selecting the already-open panel closes the menu without a switch', () => {
    const onSelectTab = renderDrawer();
    openMenu();
    fireEvent.click(screen.getByTestId('drawer-switcher-item-artifacts'));
    expect(onSelectTab).not.toHaveBeenCalled();
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('an outside click closes the menu; a click inside it does not', () => {
    renderDrawer();
    openMenu();
    fireEvent.pointerDown(screen.getByRole('menu'));
    expect(screen.getByRole('menu')).toBeTruthy();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('offers the studio tab only when the studio is live — same rule as the ⋯ menu and ⌘K', () => {
    renderDrawer(vi.fn(), visibleCategories(NO_STUDIO));
    openMenu();
    expect(itemIds()).not.toContain('builder');
    expect(itemIds().length).toBe(allTabs().length - 1);
  });

  it('without onSelectTab the title stays a plain label', () => {
    render(
      <BookmarkDrawer open pinned onPinnedChange={vi.fn()} onClose={vi.fn()} title="ARTIFACTS">
        <div>panel body</div>
      </BookmarkDrawer>,
    );
    expect(screen.getByText('ARTIFACTS')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /switch panel/i })).toBeNull();
  });
});

describe('drawer panel switcher — Escape', () => {
  // The drawer's Esc listener and the menu's are both on `document`, so
  // stopPropagation cannot separate them; the drawer owns the menu's open
  // state and yields its Esc while the menu is open.
  function renderEsc(pinned: boolean) {
    const onClose = vi.fn();
    render(
      <BookmarkDrawer
        open
        pinned={pinned}
        inset
        onPinnedChange={vi.fn()}
        onClose={onClose}
        title="ARTIFACTS"
        activeTab="artifacts"
        onSelectTab={vi.fn()}
        switcherCategories={visibleCategories(STUDIO_OPEN)}
      >
        <div>panel body</div>
      </BookmarkDrawer>,
    );
    return onClose;
  }
  const pressEsc = () => fireEvent.keyDown(document, { key: 'Escape' });

  it('unpinned: Esc with the menu open closes only the menu, not the drawer', () => {
    const onClose = renderEsc(false);
    openMenu();
    expect(screen.getByRole('menu')).toBeTruthy();
    pressEsc();
    expect(screen.queryByRole('menu')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    // The next Esc, with the menu closed, closes the transient drawer as before.
    pressEsc();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closing the drawer with the menu open does not resurrect the menu on reopen', () => {
    const props = {
      pinned: true,
      onPinnedChange: vi.fn(),
      onClose: vi.fn(),
      title: 'ARTIFACTS',
      activeTab: 'artifacts',
      onSelectTab: vi.fn(),
      switcherCategories: visibleCategories(STUDIO_OPEN),
    };
    const { rerender } = render(<BookmarkDrawer open {...props}><div /></BookmarkDrawer>);
    openMenu();
    expect(screen.getByRole('menu')).toBeTruthy();
    rerender(<BookmarkDrawer open={false} {...props}><div /></BookmarkDrawer>);
    rerender(<BookmarkDrawer open {...props}><div /></BookmarkDrawer>);
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('pinned: Esc with the menu open closes only the menu; the drawer ignores Esc', () => {
    const onClose = renderEsc(true);
    openMenu();
    pressEsc();
    expect(screen.queryByRole('menu')).toBeNull();
    pressEsc();
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe('drawer panel switcher — team room', () => {
  it('lists every team panel incl. files and manage, with live counts and zeros hidden', () => {
    const onSelectTab = vi.fn();
    render(
      <BookmarkDrawer
        open
        pinned
        onPinnedChange={vi.fn()}
        onClose={vi.fn()}
        title="MEMBERS"
        activeTab="members"
        onSelectTab={onSelectTab}
        switcherCategories={teamDrawerCategories({ members: 2, artifacts: 0, files: 3 })}
      >
        <div>panel body</div>
      </BookmarkDrawer>,
    );
    openMenu();
    expect(itemIds()).toEqual(['members', 'artifacts', 'files', 'manage']);
    expect(screen.getByTestId('drawer-switcher-item-files').textContent).toContain('3');
    expect(screen.getByTestId('drawer-switcher-item-members').textContent).toContain('2');
    // count > 0 gate: a zero must not render at all.
    expect(screen.getByTestId('drawer-switcher-item-artifacts').textContent).not.toMatch(/\d/);
    fireEvent.click(screen.getByTestId('drawer-switcher-item-manage'));
    expect(onSelectTab).toHaveBeenCalledWith('manage');
  });
});
