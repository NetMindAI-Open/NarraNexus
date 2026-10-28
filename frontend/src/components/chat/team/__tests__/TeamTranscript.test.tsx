/**
 * @file_name: TeamTranscript.test.tsx
 * @description: The message stream, and the one thing it owed the reader.
 *
 * A team room is an ASYNC space: the user gives it work and comes back. Without
 * date separators a message from Monday sits flush against one from Thursday
 * and reads as the same conversation — the private chat has had them for a long
 * time, and the room is where they matter more, not less.
 *
 * The separators are also the reason this component exists rather than a loop
 * inside the 876-line panel: "when did this happen" is a property of the
 * SEQUENCE, not of any one message, so it cannot live in the bubble.
 */
import { describe, expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, v?: Record<string, unknown>) =>
      v ? `${k}(${Object.values(v).join(',')})` : k,
  }),
}));

import { TeamTranscript } from '../TeamTranscript';

function msg(id: string, created_at: string, over: Record<string, unknown> = {}) {
  return {
    message_id: id,
    from_agent: 'agent_a',
    author_name: 'Ana',
    is_user: false,
    content: `body ${id}`,
    created_at,
    ...over,
  } as never;
}

function draw(messages: unknown[], props: Record<string, unknown> = {}) {
  render(
    <TeamTranscript
      messages={messages as never}
      userLabel="Bin"
      leadAgentId=""
      memberNames={{ agent_a: 'Ana' }}
      {...props}
    />,
  );
}

describe('TeamTranscript', () => {
  test('messages on different days are separated', () => {
    draw([msg('a', '2026-08-10T09:00:00Z'), msg('b', '2026-08-12T09:00:00Z')]);
    expect(screen.getAllByTestId(/^day-sep-/)).toHaveLength(2);
  });

  test('messages on the same day share one separator', () => {
    // Built from LOCAL time on purpose. Two UTC instants on the same UTC date
    // can be different local days — the first version of this test used
    // 01:00Z and 23:00Z, which is one day in UTC and two in UTC+8, so it
    // asserted a wrong expectation rather than finding a wrong behaviour.
    const morning = new Date(2026, 7, 12, 9, 0, 0);
    const evening = new Date(2026, 7, 12, 21, 0, 0);

    draw([msg('a', morning.toISOString()), msg('b', evening.toISOString())]);

    expect(screen.getAllByTestId(/^day-sep-/)).toHaveLength(1);
  });

  test('the day is decided in local time, not UTC', () => {
    // Two instants can be the same UTC day and different local days. Grouping
    // by the UTC date would put a separator where the reader sees none, or
    // hide one they expect — the whole point is to match the clock on the wall.
    const a = new Date('2026-08-12T23:30:00Z');
    const b = new Date('2026-08-13T00:30:00Z');
    const sameLocalDay = a.toDateString() === b.toDateString();

    draw([msg('a', a.toISOString()), msg('b', b.toISOString())]);

    expect(screen.getAllByTestId(/^day-sep-/)).toHaveLength(sameLocalDay ? 1 : 2);
  });

  test('every message still renders', () => {
    draw([msg('a', '2026-08-10T09:00:00Z'), msg('b', '2026-08-12T09:00:00Z')]);
    expect(screen.getByTestId('bubble-a')).toBeTruthy();
    expect(screen.getByTestId('bubble-b')).toBeTruthy();
  });

  test('an unparseable timestamp does not lose the message', () => {
    // A bad row must cost its separator, not its content.
    draw([msg('a', 'not-a-date')]);
    expect(screen.getByTestId('bubble-a')).toBeTruthy();
  });

  test('an empty transcript renders nothing rather than a stray separator', () => {
    draw([]);
    expect(screen.queryByTestId(/^day-sep-/)).toBeNull();
  });

  test('system lines are not given bubbles', () => {
    // The room speaking is not a member speaking; the panel renders those as
    // centred notices and they must not acquire an identity colour here.
    draw([msg('s', '2026-08-12T09:00:00Z', { msg_type: 'system_bulletin', is_platform: true })]);
    expect(screen.queryByTestId('bubble-s')).toBeNull();
  });

  test('a system line still gets its day separator', () => {
    // Otherwise a day whose only event was a stop or a bulletin change appears
    // to belong to the previous day.
    draw([msg('s', '2026-08-12T09:00:00Z', { msg_type: 'system_stop', is_platform: true })]);
    expect(screen.getAllByTestId(/^day-sep-/)).toHaveLength(1);
  });
});

describe('platform lines the server can send', () => {
  // This used to loop over a hand-written list of msg_types — a THIRD copy of
  // the server's registry, next to the component's own Set, and already two
  // types short of both. The client no longer decides: the server sends
  // `is_platform`, so what is worth testing is that the flag is obeyed, whatever
  // the type string happens to be.
  function drawOne(over: Record<string, unknown>) {
    render(
      <TeamTranscript
        messages={[msg('s', '2026-08-12T09:00:00Z', over)] as never}
        userLabel="Bin"
        leadAgentId=""
        memberNames={{ agent_a: 'Ana' }}
      />,
    );
  }

  test('a flagged line gets no bubble, whatever its type is called', () => {
    // Including a type this client has never heard of — the case the old
    // mirrored list got wrong by construction.
    drawOne({ msg_type: 'system_something_new', is_platform: true });
    expect(screen.queryByTestId('bubble-s')).toBeNull();
  });

  test('an ordinary message still gets one', () => {
    drawOne({ msg_type: 'text', is_platform: false });
    expect(screen.getByTestId('bubble-s')).toBeTruthy();
  });

  test('a message with no flag at all is treated as a member speaking', () => {
    // The honest default for a field the server did not send: a real reply
    // rendered as a system line loses its author and its bubble, while a system
    // line rendered as a reply is merely ugly. Neither is good; this one is
    // recoverable by looking at the room.
    drawOne({ msg_type: 'text' });
    expect(screen.getByTestId('bubble-s')).toBeTruthy();
  });
});
