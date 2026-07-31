/**
 * @file_name: bookmarkStrip.test.tsx
 * @date: 2026-06-11
 * @description: Tests for the atomic-tab bookmark strip + drawer shell.
 *   - registry: every category tab renders; one tab = one aria entry
 *   - status derivation: jobs failed badge / running spinner, inbox
 *     unread badge, awareness info dot
 *   - interaction: click → onOpen(tabId); active tab aria-expanded
 *   - drawer: Esc + backdrop close (slide-over), pinned mode has no
 *     backdrop
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ReactElement } from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { BookmarkStrip } from '../BookmarkStrip';
import { BookmarkDrawer } from '../BookmarkDrawer';
import { ALL_TABS, STRIP_CATEGORIES, deriveTabStatus, markTabOpened } from '../tabs';
import { useBookmarkStore } from '@/stores/bookmarkStore';

const AGENT = 'agent_strip_test';

// The strip's agent header uses router + store hooks (useNavigate); render it
// inside a router so those hooks resolve. The agent identity header itself
// stays hidden in these tests (empty config store → no agent name).
const renderStrip = (ui: ReactElement) =>
  render(<MemoryRouter>{ui}</MemoryRouter>);

beforeEach(() => {
  act(() => {
    useBookmarkStore.getState().clearAll();
  });
});

describe('tabs registry', () => {
  it('every tab id is unique and belongs to exactly one category', () => {
    const ids = ALL_TABS.map((t) => t.id);
    expect(new Set(ids).size).toBe(ids.length);
    const fromCategories = STRIP_CATEGORIES.flatMap((c) => c.tabs.map((t) => t.id));
    expect(fromCategories.sort()).toEqual([...ids].sort());
  });
});

describe('BookmarkStrip — rendering', () => {
  it('renders one button per atomic tab', () => {
    renderStrip(<BookmarkStrip agentId={AGENT} activeTab={null} onOpen={vi.fn()} />);
    for (const tab of ALL_TABS) {
      expect(screen.getByLabelText(tab.label)).toBeInTheDocument();
    }
  });

  it('marks the active tab with aria-expanded', () => {
    renderStrip(<BookmarkStrip agentId={AGENT} activeTab="jobs" onOpen={vi.fn()} />);
    expect(screen.getByLabelText('Jobs')).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByLabelText('Inbox')).toHaveAttribute('aria-expanded', 'false');
  });

  it('click calls onOpen with the tab id', () => {
    const onOpen = vi.fn();
    renderStrip(<BookmarkStrip agentId={AGENT} activeTab={null} onOpen={onOpen} />);
    fireEvent.click(screen.getByLabelText('MCP Servers'));
    expect(onOpen).toHaveBeenCalledWith('mcp');
  });
});

describe('status derivation', () => {
  it('failed job → jobs tab attention with badge count', () => {
    act(() => {
      useBookmarkStore.getState().noteJobFailed(AGENT, 'j1', 'Job One');
      useBookmarkStore.getState().noteJobFailed(AGENT, 'j2', 'Job Two');
    });
    const state = useBookmarkStore.getState().agents[AGENT];
    expect(deriveTabStatus(state, 'jobs')).toEqual({ status: 'attention', badge: 2 });
  });

  it('running job → jobs tab running (no badge)', () => {
    act(() => {
      useBookmarkStore.getState().noteJobRunning(AGENT, 'j1', 'Job One');
    });
    const state = useBookmarkStore.getState().agents[AGENT];
    expect(deriveTabStatus(state, 'jobs')).toEqual({ status: 'running' });
  });

  it('inbox unread → inbox tab attention with count; zero clears', () => {
    act(() => {
      useBookmarkStore.getState().noteInboxUnread(AGENT, 5);
    });
    let state = useBookmarkStore.getState().agents[AGENT];
    expect(deriveTabStatus(state, 'inbox')).toEqual({ status: 'attention', badge: 5 });

    act(() => {
      useBookmarkStore.getState().noteInboxUnread(AGENT, 0);
    });
    state = useBookmarkStore.getState().agents[AGENT];
    expect(deriveTabStatus(state, 'inbox')).toEqual({ status: 'none' });
  });

  it('awareness external update → info dot; markTabOpened clears it', () => {
    act(() => {
      useBookmarkStore.getState().noteProfileUpdate(AGENT, 'awareness');
    });
    let state = useBookmarkStore.getState().agents[AGENT];
    expect(deriveTabStatus(state, 'awareness')).toEqual({ status: 'info' });

    act(() => {
      markTabOpened(AGENT, 'awareness');
    });
    state = useBookmarkStore.getState().agents[AGENT];
    expect(deriveTabStatus(state, 'awareness')).toEqual({ status: 'none' });
  });

  it('attention is NOT cleared by opening (badge semantics)', () => {
    act(() => {
      useBookmarkStore.getState().noteJobFailed(AGENT, 'j1', 'Job One');
      markTabOpened(AGENT, 'jobs');
    });
    const state = useBookmarkStore.getState().agents[AGENT];
    expect(deriveTabStatus(state, 'jobs').status).toBe('attention');
  });
});

describe('BookmarkDrawer — close behaviors', () => {
  it('calls onClose on Escape in slide-over mode', () => {
    const onClose = vi.fn();
    render(
      <BookmarkDrawer open pinned={false} onPinnedChange={vi.fn()} onClose={onClose} title="Jobs">
        <div>content</div>
      </BookmarkDrawer>,
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('calls onClose when clicking the backdrop', () => {
    const onClose = vi.fn();
    render(
      <BookmarkDrawer open pinned={false} onPinnedChange={vi.fn()} onClose={onClose} title="Jobs">
        <div>content</div>
      </BookmarkDrawer>,
    );
    const backdrop = document.body.querySelector('[data-drawer-backdrop]');
    expect(backdrop).toBeTruthy();
    fireEvent.click(backdrop!);
    expect(onClose).toHaveBeenCalled();
  });

  it('renders no backdrop in pinned mode', () => {
    render(
      <BookmarkDrawer open pinned onPinnedChange={vi.fn()} onClose={vi.fn()} title="Jobs">
        <div>content</div>
      </BookmarkDrawer>,
    );
    expect(document.body.querySelector('[data-drawer-backdrop]')).toBeNull();
  });
});

describe('BookmarkDrawer — the strip is never covered', () => {
  // Regression: the slide-over used to be `right-0` with a full-page
  // `inset-0` backdrop, so it sat ON TOP of the 64px bookmark strip AND ate
  // its clicks. Opening Awareness then meant closing it before any other tab
  // could be reached. Both the panel and the backdrop must now stop short of
  // the reserved edge.
  const RESERVE = 76;

  const renderSlideOver = () =>
    render(
      <BookmarkDrawer
        open
        pinned={false}
        onPinnedChange={vi.fn()}
        onClose={vi.fn()}
        title="Awareness"
        edgeReservePx={RESERVE}
      >
        <div>content</div>
      </BookmarkDrawer>,
    );

  it('insets the backdrop by the reserved edge so strip clicks reach the strip', () => {
    renderSlideOver();
    const backdrop = document.body.querySelector('[data-drawer-backdrop]') as HTMLElement;
    expect(backdrop.style.right).toBe(`${RESERVE}px`);
  });

  it('insets the panel itself by the reserved edge', () => {
    renderSlideOver();
    const panel = document.body.querySelector('[role="dialog"]') as HTMLElement;
    // The panel carries the offset directly — there is no wrapper. It is
    // `position: fixed` in place rather than portalled, so that toggling pin
    // doesn't move it in the DOM (which would remount the panel inside).
    expect(panel.style.right).toBe(`${RESERVE}px`);
  });

  it('is not aria-modal — the strip beside it stays operable', () => {
    renderSlideOver();
    const panel = document.body.querySelector('[role="dialog"]') as HTMLElement;
    expect(panel.getAttribute('aria-modal')).toBeNull();
  });

  it('defaults to reserving nothing when no strip is present', () => {
    render(
      <BookmarkDrawer open pinned={false} onPinnedChange={vi.fn()} onClose={vi.fn()} title="Jobs">
        <div>content</div>
      </BookmarkDrawer>,
    );
    const backdrop = document.body.querySelector('[data-drawer-backdrop]') as HTMLElement;
    expect(backdrop.style.right).toBe('0px');
  });
});
