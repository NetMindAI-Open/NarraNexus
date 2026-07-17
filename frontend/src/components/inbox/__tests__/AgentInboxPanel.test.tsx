/**
 * AgentInboxPanel message-card tests.
 *
 * Covers the chat-list → card-list redesign inside an expanded room:
 * every message renders as its own card, the sender identity is
 * visually distinguished (initials dot + per-sender accent, stable per
 * sender and different across senders), and the sender name + relative
 * time are rendered in the card header.
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const markAgentRoomReadMock = vi.fn();
const getBusFailuresMock = vi.fn();
const getNoticesMock = vi.fn();
const refreshAgentInboxMock = vi.fn();

vi.mock('@/lib/api', () => ({
  api: {
    markAgentRoomRead: (...a: unknown[]) => markAgentRoomReadMock(...a),
    getBusFailures: (...a: unknown[]) => getBusFailuresMock(...a),
    getNotices: (...a: unknown[]) => getNoticesMock(...a),
  },
}));

vi.mock('@/stores', () => ({
  useConfigStore: () => ({ agentId: 'agent_me' }),
  usePreloadStore: () => ({
    agentInboxRooms: ROOMS,
    agentInboxUnreadCount: 0,
    agentInboxLoading: false,
    refreshAgentInbox: refreshAgentInboxMock,
  }),
}));

import { AgentInboxPanel } from '@/components/inbox/AgentInboxPanel';

const ROOMS = [
  {
    room_id: 'room_1',
    room_name: 'Deal Room',
    members: [
      { agent_id: 'agent_alice', agent_name: 'Alice Analyst' },
      { agent_id: 'agent_bob', agent_name: 'Bob Broker' },
    ],
    unread_count: 0,
    latest_at: '2026-07-13 08:00:00',
    messages: [
      {
        message_id: 'msg_a1',
        sender_id: 'agent_alice',
        sender_name: 'Alice Analyst',
        content: 'Market summary is ready.',
        is_read: true,
        created_at: '2026-07-13 07:00:00',
      },
      {
        message_id: 'msg_b1',
        sender_id: 'agent_bob',
        sender_name: 'Bob Broker',
        content: 'Thanks, reviewing now.',
        is_read: true,
        created_at: '2026-07-13 07:30:00',
      },
      {
        message_id: 'msg_a2',
        sender_id: 'agent_alice',
        sender_name: 'Alice Analyst',
        content: 'Ping me with questions.',
        is_read: true,
        created_at: '2026-07-13 08:00:00',
      },
    ],
  },
];

async function expandRoom() {
  render(<AgentInboxPanel />);
  fireEvent.click(screen.getByText('Deal Room'));
  return screen.findByTestId('inbox-message-card-msg_a1');
}

beforeEach(() => {
  markAgentRoomReadMock.mockReset();
  getBusFailuresMock.mockReset();
  getNoticesMock.mockReset();
  getBusFailuresMock.mockResolvedValue({ success: true, failures: [] });
  getNoticesMock.mockResolvedValue({ success: true, notices: [], unread_count: 0 });
});

describe('AgentInboxPanel message cards', () => {
  test('each message renders as its own card', async () => {
    await expandRoom();
    expect(screen.getByTestId('inbox-message-card-msg_a1')).toBeInTheDocument();
    expect(screen.getByTestId('inbox-message-card-msg_b1')).toBeInTheDocument();
    expect(screen.getByTestId('inbox-message-card-msg_a2')).toBeInTheDocument();
  });

  test('card header shows sender name and relative time', async () => {
    await expandRoom();
    const card = screen.getByTestId('inbox-message-card-msg_b1');
    expect(card).toHaveTextContent('Bob Broker');
    // formatRelativeTime output varies with the clock; assert the slot exists.
    expect(card.querySelector('time, [data-testid="inbox-message-time"]')).toBeTruthy();
  });

  test('sender accent is stable per sender and differs across senders', async () => {
    await expandRoom();
    const a1 = screen.getByTestId('inbox-message-card-msg_a1');
    const a2 = screen.getByTestId('inbox-message-card-msg_a2');
    const b1 = screen.getByTestId('inbox-message-card-msg_b1');
    expect(a1.className).toBe(a2.className);
    expect(a1.className).not.toBe(b1.className);
  });

  test('avatar dot shows sender initials', async () => {
    await expandRoom();
    const a1 = screen.getByTestId('inbox-message-card-msg_a1');
    const b1 = screen.getByTestId('inbox-message-card-msg_b1');
    expect(a1).toHaveTextContent('AA'); // Alice Analyst
    expect(b1).toHaveTextContent('BB'); // Bob Broker
  });
});
