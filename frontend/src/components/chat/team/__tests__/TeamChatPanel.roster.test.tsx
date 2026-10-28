/**
 * @file_name: TeamChatPanel.roster.test.tsx
 * @description: The two-pane team room's contract.
 *
 * Two decisions are pinned here, both learned from the folded console this
 * layout replaces:
 *   1. "who is in this room and what are they doing" is STANDING chrome — the
 *      roster lists every member without anyone expanding anything
 *   2. the transcript only ever carries a typing bubble for a member working
 *      RIGHT NOW; a finished turn leaves nothing behind, because its trace now
 *      lives one click away in the roster instead of piling up in the flow
 * plus the wiring between them: clicking a typing bubble opens that member's
 * process in the roster (one selection, two surfaces).
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { DRAWER_PINNED_KEY } from '@/components/layout/drawerLayout';

const getTeamChatMock = vi.fn();
const getEventLogMock = vi.fn();
const updateTeamMock = vi.fn();

vi.mock('@/lib/api', () => ({
  api: {
    getTeamChat: (...a: unknown[]) => getTeamChatMock(...a),
    getEventLog: (...a: unknown[]) => getEventLogMock(...a),
    updateTeam: (...a: unknown[]) => updateTeamMock(...a),
    getTranscriptionAvailability: () => Promise.resolve({ available: true, reason: '' }),
  },
}));

// The store hooks are selector-based; the fixtures are read at call time (the
// factory itself runs before the module body, so it must not touch them).
// Stable identities: a fresh `notePatrol` per selector call would change the
// room's `refresh` every render and re-arm its poll effect without end.
const PATROL_BY_TEAM: Record<string, boolean> = {};
const NOTE_PATROL = () => {};

vi.mock('@/stores', () => ({
  useTeamsStore: (select: (s: unknown) => unknown) =>
    select({ teams: TEAMS, patrolByTeam: PATROL_BY_TEAM, notePatrol: NOTE_PATROL }),
  useConfigStore: (select: (s: unknown) => unknown) =>
    select({ agents: AGENTS, displayName: 'Bin', userId: 'usr_1' }),
  useChatStore: (select: (s: unknown) => unknown) => select({ workspaceRefreshTick: 0 }),
}));

vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useNavigate: () => () => {},
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, v?: unknown) =>
      v && typeof v === 'object' ? `${k}(${Object.values(v).join(',')})` : k,
  }),
}));

import { TeamChatPanel } from '../TeamChatPanel';

const AGENTS = [
  { agent_id: 'a1', name: 'Ana' },
  { agent_id: 'a2', name: 'Bruno' },
  { agent_id: 'a3', name: 'Cy' },
];

const TEAMS = [
  {
    team: { team_id: 't1', name: 'Desk', owner_user_id: 'usr_1', source: 'local' },
    member_agent_ids: ['a1', 'a2', 'a3'],
  },
];

const MESSAGE = {
  message_id: 'm1',
  from_agent: 'usr_usr_1',
  author_name: 'Bin',
  is_user: true,
  content: 'status?',
  created_at: '2026-07-30T09:00:00Z',
};

const RUNNING = {
  agent_id: 'a1',
  status: 'running' as const,
  phase: 'thinking',
  started_at: '2026-07-30T08:59:00Z',
};

const IDLE_WITH_TRACE = {
  agent_id: 'a2',
  status: 'idle' as const,
  started_at: '2026-07-30T08:50:00Z',
  finished_at: '2026-07-30T08:59:30Z',
  event_id: 'evt_1',
  steps: { items: [{ phase: 'thinking', at: '2026-07-30T08:50:10Z' }], dropped: 0 },
};

function typingButtons() {
  return screen
    .queryAllByRole('button')
    .filter((b) => (b.getAttribute('aria-label') || '').startsWith('chat.team.typing'));
}

/** Assert the transcript's typing-indicator count, once the room has settled.
 *
 * Only meaningful AFTER `renderRoom` — its anchor is what proves the fetch
 * landed. A `waitFor` here cannot carry that weight on its own: `waitFor`
 * polls until the assertion first PASSES, so `toHaveLength(0)` is already
 * true before anything renders and returns immediately. Waiting only works
 * for counts > 0; the zero case needs the positive anchor below.
 */
async function settledTypingButtons(count: number) {
  await waitFor(() => expect(typingButtons()).toHaveLength(count));
  return typingButtons();
}

/**
 * `anchor` must be something that only exists once `getTeamChat` RESOLVED.
 *
 * The roster rows are the wrong anchor: they come from the synchronous store
 * mock, so they are on screen before the fetch lands — the transcript (typing
 * indicators, message bubbles) settles in a later commit. Anchoring on a
 * roster row made every transcript assertion a race: green on a fast machine,
 * red on a loaded CI runner (observed 2026-08-07), and — worse — permanently
 * green for any assertion that a transcript element is ABSENT.
 */
async function renderRoom(
  activity: unknown[],
  messages: unknown[] = [MESSAGE],
  anchor: string = MESSAGE.content,
) {
  getTeamChatMock.mockResolvedValue({
    success: true,
    messages,
    activity,
    lead_agent_id: 'a1',
  });
  const view = render(<TeamChatPanel teamId="t1" />);
  await screen.findByText(anchor);
  return view;
}

beforeEach(() => {
  // The drawer's pin preference is a shared persisted key; isolate cases.
  window.localStorage.clear();
  getTeamChatMock.mockReset();
  getEventLogMock.mockReset();
  updateTeamMock.mockReset();
  getEventLogMock.mockResolvedValue({ success: true, timeline: [] });
  // jsdom has no layout, so the transcript's "keep the tail in view" call would
  // throw before anything renders.
  Element.prototype.scrollIntoView = () => {};
});

describe('TeamChatPanel · two-pane room', () => {
  test('renders the roster panel with every member', async () => {
    await renderRoom([RUNNING, IDLE_WITH_TRACE]);

    // The roster is the drawer's members panel; it opens by default only
    // on non-mobile AND with the pinned preference on (the storage default
    // — jsdom's matchMedia stub answers false to the max-width query, so
    // useIsMobile is false, and no stored key means pinned).
    // Both members have a row — the idle one is not hidden just because it
    // has nothing in flight.
    expect(within(screen.getByTestId('roster-row-a1')).getByText('Ana')).toBeTruthy();
    expect(within(screen.getByTestId('roster-row-a2')).getByText('Bruno')).toBeTruthy();
  });

  test('typing indicator appears only for running members', async () => {
    await renderRoom([RUNNING, IDLE_WITH_TRACE]);

    const typing = await settledTypingButtons(1);
    expect(typing[0].getAttribute('aria-label')).toBe('chat.team.typing(Ana)');
  });

  test('clicking the typing indicator expands that member in the roster', async () => {
    await renderRoom([RUNNING, IDLE_WITH_TRACE]);

    fireEvent.click((await settledTypingButtons(1))[0]);

    // Every roster surface showing that member reflects the same selection.
    const rows = screen.getAllByTestId('roster-row-a1');
    expect(rows.length).toBeGreaterThan(0);
    for (const row of rows) expect(row.getAttribute('aria-expanded')).toBe('true');
    expect(
      screen.getAllByTestId('roster-row-a2').every((r) => r.getAttribute('aria-expanded') === 'false'),
    ).toBe(true);
  });

  test('no lingering bubbles for idle members with steps', async () => {
    await renderRoom([
      IDLE_WITH_TRACE,
      { ...IDLE_WITH_TRACE, agent_id: 'a1', event_id: 'evt_2' },
    ]);

    await settledTypingButtons(0);
  });
});

/**
 * The addressing rules used to be a permanent grey banner above the transcript.
 * They now live in two on-demand places: the empty room's hero, and a `?`
 * popover in the member bar that works for the whole life of the room.
 */
describe('TeamChatPanel · discoverable panel chrome', () => {
  // The team-room redesign left the roster/work-board and the bulletin behind
  // bare, unlabeled icons — the reason users reported the bulletin / work board
  // as "gone". The toggles must carry a VISIBLE text label, not just a tooltip.
  test('the team-management toggle shows a visible label, not just an icon', async () => {
    // 2026-09-03: the bulletin lives inside the management tab now, so the
    // labelled entry point is the tab's toggle.
    await renderRoom([RUNNING]);
    const toggle = screen.getByTestId('manage-toggle');
    // getByText finds a rendered text node — a `title`/`aria-label` would not
    // satisfy it, so this distinguishes a visible label from a tooltip.
    expect(within(toggle).getByText('chat.team.manage.title')).toBeTruthy();
    expect(screen.queryByTestId('bulletin-toggle')).toBeNull();
  });

  test('the members (roster/work-board) toggle shows a visible label', async () => {
    await renderRoom([RUNNING]);
    const toggle = screen.getByTestId('members-toggle');
    // This only asserts the label text NODE renders — jsdom has no layout, so
    // it cannot see clipping or wrapping. That the labels stay reachable on a
    // phone (the member bar is flex-wrap) is a real-device check, not this one.
    expect(within(toggle).getByText('chat.team.roster.title')).toBeTruthy();
  });

  test('the workspace (artifacts/work board) toggle shows a visible label', async () => {
    await renderRoom([RUNNING]);
    const toggle = screen.getByTestId('artifacts-toggle');
    expect(within(toggle).getByText('rail.artifacts')).toBeTruthy();
  });
});

describe('TeamChatPanel · set lead from the roster', () => {
  // The observable is that `set-lead-<id>` only renders for a NON-lead member,
  // so the optimistic write must first make the affordance DISAPPEAR (a2 became
  // lead) and the rollback must bring it BACK (a2 is not lead again). A deferred
  // promise separates the two so the intermediate state actually renders — a
  // plain resolved mock batches both sets into one render and would let an
  // empty handler pass.
  test('optimistic set then rollback on a failed PATCH', async () => {
    let resolvePatch!: (v: unknown) => void;
    updateTeamMock.mockReturnValue(
      new Promise((r) => {
        resolvePatch = r;
      }),
    );
    await renderRoom([RUNNING, IDLE_WITH_TRACE]); // lead starts as a1

    fireEvent.click(screen.getByTestId('roster-row-a2'));
    fireEvent.click(await screen.findByTestId('set-lead-a2'));

    // Optimistic: a2 is now lead, so its set-lead affordance is gone.
    await waitFor(() =>
      expect(screen.queryByTestId('set-lead-a2')).toBeNull(),
    );
    expect(updateTeamMock).toHaveBeenCalledWith('t1', { lead_agent_id: 'a2' });

    // The PATCH fails → rollback → a2 is not lead again → affordance returns.
    resolvePatch({ success: false });
    await waitFor(() =>
      expect(screen.getByTestId('set-lead-a2')).toBeTruthy(),
    );
  });

  test('a second click while the first PATCH is in flight is ignored', async () => {
    let resolveFirst!: (v: unknown) => void;
    updateTeamMock.mockReturnValueOnce(
      new Promise((r) => {
        resolveFirst = r;
      }),
    );
    await renderRoom([RUNNING, IDLE_WITH_TRACE]);

    fireEvent.click(screen.getByTestId('roster-row-a2'));
    fireEvent.click(await screen.findByTestId('set-lead-a2'));
    await waitFor(() => expect(updateTeamMock).toHaveBeenCalledTimes(1));

    // While a2's PATCH is unresolved, try to set a different member as lead.
    fireEvent.click(screen.getByTestId('roster-row-a3'));
    fireEvent.click(await screen.findByTestId('set-lead-a3'));

    // The in-flight guard rejects it: still exactly one PATCH. (a3's row is
    // expanded and still not lead, so its set-lead affordance is on screen.)
    expect(updateTeamMock).toHaveBeenCalledTimes(1);

    // ...and the guard RELEASES once the first PATCH settles: a3's set-lead now
    // goes through. Without the `finally { settingLeadRef = false }`, set-lead
    // would be usable exactly once per mount. The first PATCH succeeds (a2 stays
    // lead → no state change, so no act warning); `findByTestId` awaits the
    // microtask that clears the guard.
    updateTeamMock.mockResolvedValue({ success: true });
    resolveFirst({ success: true });
    fireEvent.click(await screen.findByTestId('set-lead-a3'));
    await waitFor(() => expect(updateTeamMock).toHaveBeenCalledTimes(2));
    expect(updateTeamMock).toHaveBeenLastCalledWith('t1', { lead_agent_id: 'a3' });
  });
});

describe('TeamChatPanel · addressing help', () => {
  test('help button toggles the guide popover', async () => {
    await renderRoom([RUNNING, IDLE_WITH_TRACE]);

    // Not standing chrome — nothing on screen until the user asks.
    expect(screen.queryByText('chat.team.guide.plainTitle')).toBeNull();

    fireEvent.click(screen.getByLabelText('chat.team.guide.title'));
    expect(screen.getByText('chat.team.guide.plainTitle')).toBeTruthy();
    expect(screen.getByText('chat.team.guide.plainWithLead(Ana)')).toBeTruthy();

    // Clicking anywhere else dismisses it, like any popover.
    fireEvent.mouseDown(document.body);
    expect(screen.queryByText('chat.team.guide.plainTitle')).toBeNull();
  });

  test('an empty room shows the hero instead of the transcript', async () => {
    // No messages, so the hero — which also only renders after the fetch —
    // is this case's proof that the room settled.
    await renderRoom([], [], 'chat.team.guide.plainTitle');

    // The room names itself in the hero on top of the member bar's copy.
    expect(screen.getAllByText('Desk')).toHaveLength(2);
    expect(screen.getByText('chat.team.guide.plainTitle')).toBeTruthy();
    expect(screen.getByText('chat.team.guide.relay')).toBeTruthy();
  });
});

describe('TeamChatPanel · per-message reasoning disclosure', () => {
  const AGENT_REPLY = {
    message_id: 'm2',
    from_agent: 'a2',
    author_name: 'Bruno',
    is_user: false,
    content: 'done — summary attached',
    event_id: 'evt_9',
    created_at: '2026-07-30T09:01:00Z',
  };
  const LEGACY_REPLY = {
    message_id: 'm3',
    from_agent: 'a1',
    author_name: 'Ana',
    is_user: false,
    content: 'older reply from before the column existed',
    event_id: null,
    created_at: '2026-07-30T09:02:00Z',
  };

  test('an agent reply with an event_id offers the disclosure; legacy and user rows do not', async () => {
    await renderRoom([RUNNING, IDLE_WITH_TRACE], [MESSAGE, AGENT_REPLY, LEGACY_REPLY]);

    // Exactly one bubble carries the affordance — m2. The user message and the
    // legacy (event_id: null) reply degrade to a plain bubble, no dead button.
    expect(screen.getAllByText('chat.message.viewReasoning')).toHaveLength(1);
  });

  test('opening the disclosure fetches that turn and renders its process', async () => {
    getEventLogMock.mockResolvedValue({
      success: true,
      // monologue: narration renders open, provider reasoning collapses
      // (2026-08-30). This case is about the disclosure fetching, not the tier.
      timeline: [{ type: 'thinking', content: 'weighing options', monologue: true }],
    });
    await renderRoom([RUNNING, IDLE_WITH_TRACE], [MESSAGE, AGENT_REPLY]);

    fireEvent.click(screen.getByText('chat.message.viewReasoning'));
    expect(await screen.findByText('weighing options')).toBeTruthy();
    expect(getEventLogMock).toHaveBeenCalledWith('a2', 'evt_9');
  });


});

describe('TeamChatPanel · drawer defaults and switching', () => {
  test('an unpinned preference means the drawer does NOT auto-open', async () => {
    // Shared preference with single chat — an unpinned user must not be
    // greeted by a transient drawer whose backdrop eats their first click.
    window.localStorage.setItem(DRAWER_PINNED_KEY, '0');
    await renderRoom([RUNNING, IDLE_WITH_TRACE]);
    expect(screen.queryByTestId('roster-row-a1')).toBeNull();
  });

  test('the drawer switches panels: members → artifacts via the member-bar toggle', async () => {
    await renderRoom([RUNNING, IDLE_WITH_TRACE]);
    // Member-bar path; the title-switcher path is covered below.
    fireEvent.click(screen.getByTestId('artifacts-toggle'));
    // The members rows are gone; the artifacts panel's empty state shows.
    expect(screen.queryByTestId('roster-row-a1')).toBeNull();
    expect(screen.getByText('chat.team.workspace.artifactsHint')).toBeTruthy();
    // And back to members via the top-bar toggle.
    fireEvent.click(screen.getAllByLabelText('chat.team.roster.title')[0]);
    expect(screen.getByTestId('roster-row-a1')).toBeTruthy();
  });

  test('shared files have their own member-bar entry (beside the title switcher)', async () => {
    // Both entry points coexist: the drawer title switcher AND this toggle.
    await renderRoom([RUNNING, IDLE_WITH_TRACE]);
    fireEvent.click(screen.getByTestId('files-toggle'));
    expect(screen.queryByTestId('roster-row-a1')).toBeNull();
    expect(screen.getByText('chat.team.workspace.filesHint')).toBeTruthy();
  });
  test('the drawer title switcher reaches every team panel, incl. files and manage', async () => {
    // Owner-required: a pinned drawer switches its own content from its
    // title, without a trip back to the member bar.
    await renderRoom([RUNNING, IDLE_WITH_TRACE]);
    expect(screen.getByTestId('roster-row-a1')).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/bookmarks\.drawer\.switchPanel/));
    const ids = screen
      .getAllByRole('menuitemradio')
      .map((el) => el.getAttribute('data-testid'));
    expect(ids).toEqual([
      'drawer-switcher-item-members',
      'drawer-switcher-item-artifacts',
      'drawer-switcher-item-files',
      'drawer-switcher-item-manage',
    ]);
    fireEvent.click(screen.getByTestId('drawer-switcher-item-files'));
    expect(screen.queryByTestId('roster-row-a1')).toBeNull();
    expect(screen.getByText('chat.team.workspace.filesHint')).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/bookmarks\.drawer\.switchPanel/));
    fireEvent.click(screen.getByTestId('drawer-switcher-item-members'));
    expect(screen.getByTestId('roster-row-a1')).toBeTruthy();
  });
});
