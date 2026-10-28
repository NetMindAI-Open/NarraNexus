/**
 * @file_name: TeamChatPanel.composer.test.tsx
 * @description: What the room's composer owes the person typing into it.
 *
 * Three things, all of which it failed at:
 *
 * **A half-typed message survives leaving.** The private chat has kept drafts
 * per agent for a long time (`chatDrafts` + `<Composer>`); the room's composer
 * was a bare `useState`, so switching rooms or navigating away silently threw
 * away whatever was in it. In a room you are meant to hand work to and leave,
 * that is the wrong half of the product to lose.
 *
 * **Enter must not send a half-composed word.** The room's textarea had no IME
 * composition guard, so pressing Enter to accept a Pinyin or Kana candidate
 * sent the message instead — for the languages this project is used in, that is
 * the composer being broken for a whole class of input, not an edge case. The
 * private chat's Composer has had the guard, including the short
 * just-finished-composition window, for exactly this reason.
 *
 * **A failure has to be visible.** A failed send silently restored the text —
 * indistinguishable from the Enter key not registering. A failed upload was
 * documented as "silent — the user can retry", which assumes the user knows
 * there is something to retry.
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

const sendTeamChat = vi.fn();
const uploadTeamChatAttachment = vi.fn();

vi.mock('@/lib/api', () => ({
  api: {
    getTeamChat: () =>
      Promise.resolve({ success: true, messages: [], activity: [], lead_agent_id: null }),
    getEventLog: () => Promise.resolve({ success: true, events: [] }),
    getTranscriptionAvailability: () => Promise.resolve({ available: true, reason: '' }),
    listTeamArtifacts: () => Promise.resolve([]),
    listTeamFiles: () => Promise.resolve([]),
    listTeamArtifactTurns: () => Promise.resolve({}),
    sendTeamChat: (...a: unknown[]) => sendTeamChat(...a),
    uploadTeamChatAttachment: (...a: unknown[]) => uploadTeamChatAttachment(...a),
  },
}));

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

import { TeamChatPanel } from '../TeamChatPanel';
import { getTeamDraft } from '@/lib/chatDrafts';

const AGENTS = [
  { agent_id: 'a1', name: 'Ana Silva' },
  { agent_id: 'a3', name: 'Researcher' },
  // Same display name as a3 — two clones of an agent, or two that kept the
  // default. The case the name-keyed map silently collapsed.
  { agent_id: 'a4', name: 'Researcher' },
];
const TEAMS = [
  {
    team: { team_id: 't1', name: 'Desk', owner_user_id: 'usr_1', source: 'local' },
    member_agent_ids: ['a1'],
  },
  {
    team: { team_id: 't2', name: 'Lab', owner_user_id: 'usr_1', source: 'local' },
    member_agent_ids: ['a1'],
  },
  {
    team: { team_id: 't3', name: 'Twins', owner_user_id: 'usr_1', source: 'local' },
    member_agent_ids: ['a3', 'a4'],
  },
];

function composer(): HTMLTextAreaElement {
  return screen.getByPlaceholderText(/./, { selector: 'textarea' }) as HTMLTextAreaElement;
}

function type(el: HTMLTextAreaElement, value: string) {
  fireEvent.change(el, { target: { value } });
}

beforeEach(() => {
  localStorage.clear();
  sendTeamChat.mockReset().mockResolvedValue({ success: true });
  uploadTeamChatAttachment.mockReset().mockResolvedValue({ success: true, attachment: null });
});

describe('the room composer keeps what you typed', () => {
  test('a draft survives leaving the room', async () => {
    const view = render(<TeamChatPanel teamId="t1" />);
    type(composer(), 'half a thought');

    view.unmount();

    expect(getTeamDraft('t1')).toBe('half a thought');
  });

  test('coming back restores it', async () => {
    render(<TeamChatPanel teamId="t1" />).unmount();
    // Simulate a previous visit's flush.
    const first = render(<TeamChatPanel teamId="t1" />);
    type(composer(), 'where was I');
    first.unmount();

    render(<TeamChatPanel teamId="t1" />);

    expect(composer().value).toBe('where was I');
  });

  test('each room has its own draft', async () => {
    const a = render(<TeamChatPanel teamId="t1" />);
    type(composer(), 'for the desk');
    a.unmount();

    const b = render(<TeamChatPanel teamId="t2" />);
    expect(composer().value).toBe('');
    type(composer(), 'for the lab');
    b.unmount();

    expect(getTeamDraft('t1')).toBe('for the desk');
    expect(getTeamDraft('t2')).toBe('for the lab');
  });

  test('switching rooms without unmounting files the draft under the room it was typed in', async () => {
    // The route change re-renders this component with the new room while the
    // old text is still in state — two updates, two different commits. Anything
    // persisting the draft has to know which of the two it is holding, or the
    // first save after a switch files the previous room's words under the new
    // room's name, and the user finds them in the wrong place.
    const view = render(<TeamChatPanel teamId="t1" />);
    type(composer(), 'meant for the desk');

    view.rerender(<TeamChatPanel teamId="t2" />);
    await act(async () => {});

    expect(getTeamDraft('t1')).toBe('meant for the desk');
    expect(getTeamDraft('t2')).toBe('');
    expect(composer().value).toBe('');
  });

  test('switching back brings the draft with it', async () => {
    const view = render(<TeamChatPanel teamId="t1" />);
    type(composer(), 'meant for the desk');
    view.rerender(<TeamChatPanel teamId="t2" />);
    await act(async () => {});

    view.rerender(<TeamChatPanel teamId="t1" />);
    await act(async () => {});

    expect(composer().value).toBe('meant for the desk');
  });

  test('a sent message is not still sitting there next time', async () => {
    const view = render(<TeamChatPanel teamId="t1" />);
    type(composer(), 'go on then');
    fireEvent.keyDown(composer(), { key: 'Enter' });

    await waitFor(() => expect(sendTeamChat).toHaveBeenCalled());
    await act(async () => {});
    view.unmount();

    expect(getTeamDraft('t1')).toBe('');
  });
});

describe('Enter does not interrupt an IME', () => {
  test('Enter while composing does not send', () => {
    // Accepting a Pinyin candidate is an Enter press. Sending on it makes the
    // composer unusable for the languages this project is written in.
    render(<TeamChatPanel teamId="t1" />);
    const el = composer();
    type(el, 'ni hao');

    fireEvent.compositionStart(el);
    fireEvent.keyDown(el, { key: 'Enter', isComposing: true });

    expect(sendTeamChat).not.toHaveBeenCalled();
  });

  test('Enter immediately after composition ends does not send either', async () => {
    // Some IMEs fire compositionend BEFORE the keydown that accepted the
    // candidate. The flag alone does not cover this: it is cleared on a
    // macrotask, so once that has run only the grace window is left standing
    // between the last Enter of every composed word and an accidental send.
    //
    // The `await` is what makes this test about the WINDOW rather than the
    // flag — without it the flag is still set and the assertion passes for the
    // wrong reason.
    // Fake timers, so the grace window is measured against a CLOCK THIS TEST
    // CONTROLS. With real time, `advance` below has to run the flag's 0ms timer
    // without also burning 100ms of wall clock — which held on an idle machine
    // and failed on a loaded one. A test that depends on how busy the CI box is
    // teaches people to re-run rather than to read.
    vi.useFakeTimers({ shouldAdvanceTime: false });
    try {
      render(<TeamChatPanel teamId="t1" />);
      const el = composer();
      type(el, '你好');

      fireEvent.compositionStart(el);
      fireEvent.compositionEnd(el);
      // Runs the macrotask that clears the flag, at t+0: past the flag, still
      // inside the window. Exactly the state only the window covers.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      fireEvent.keyDown(el, { key: 'Enter' });

      expect(sendTeamChat).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  test('Enter on ordinary typing still sends', async () => {
    render(<TeamChatPanel teamId="t1" />);
    const el = composer();
    type(el, 'plain text');

    fireEvent.keyDown(el, { key: 'Enter' });

    await waitFor(() => expect(sendTeamChat).toHaveBeenCalled());
  });
});

describe('a failure is visible', () => {
  test('a failed send says so and keeps the text', async () => {
    // Restoring the text silently is indistinguishable from the Enter key not
    // having registered — the user retypes, or sends twice.
    sendTeamChat.mockRejectedValue(new Error('network'));
    render(<TeamChatPanel teamId="t1" />);
    type(composer(), 'important');

    fireEvent.keyDown(composer(), { key: 'Enter' });

    await screen.findByTestId('composer-error');
    expect(composer().value).toBe('important');
  });

  test('a failed upload says so', async () => {
    uploadTeamChatAttachment.mockRejectedValue(new Error('too large'));
    render(<TeamChatPanel teamId="t1" />);

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['x'], 'notes.txt', { type: 'text/plain' });
    fireEvent.change(input, { target: { files: [file] } });

    await screen.findByTestId('composer-error');
  });

  test('an upload the server refuses says so too', async () => {
    // Not an exception: `success: false` used to fall through to "no chip
    // appeared", which looks exactly like an upload still in flight.
    uploadTeamChatAttachment.mockResolvedValue({ success: false, attachment: null });
    render(<TeamChatPanel teamId="t1" />);

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['x'], 'notes.txt', { type: 'text/plain' });
    fireEvent.change(input, { target: { files: [file] } });

    await screen.findByTestId('composer-error');
  });

  test('the error clears when the next attempt starts', async () => {
    // A stale error next to a message that did send is its own lie.
    sendTeamChat.mockRejectedValueOnce(new Error('network'));
    render(<TeamChatPanel teamId="t1" />);
    type(composer(), 'important');
    fireEvent.keyDown(composer(), { key: 'Enter' });
    await screen.findByTestId('composer-error');

    fireEvent.keyDown(composer(), { key: 'Enter' });

    await waitFor(() => expect(screen.queryByTestId('composer-error')).toBeNull());
  });
});

describe('who a message wakes', () => {
  test('a mention resolves to the member id, by first name', () => {
    // The composer's own autocomplete inserts `@Ana Silva`; the token stops at
    // the space, so this is the ordinary case rather than an edge one.
    render(<TeamChatPanel teamId="t1" />);
    type(composer(), 'hey @Ana can you look');

    fireEvent.keyDown(composer(), { key: 'Enter' });

    return waitFor(() => {
      expect(sendTeamChat).toHaveBeenCalledWith('t1', 'hey @Ana can you look', ['a1'], []);
    });
  });

  test('two members sharing a display name are BOTH woken', () => {
    // Keying the name→id map by display name dropped all but the last, so one
    // Researcher was woken and the other never learned they had been named —
    // while the room highlighted the word for both. The server iterates
    // members, not names; so does this now.
    render(<TeamChatPanel teamId="t3" />);
    type(composer(), '@Researcher please take this');

    fireEvent.keyDown(composer(), { key: 'Enter' });

    return waitFor(() => {
      expect(sendTeamChat).toHaveBeenCalledWith(
        't3',
        '@Researcher please take this',
        ['a3', 'a4'],
        [],
      );
    });
  });
});
