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

describe('readability upgrade (bug: 浏览器阅读不友好)', () => {
  test('long message content is clamped with a show-more toggle', async () => {
    ROOMS[0].messages.push({
      message_id: 'msg_long',
      sender_id: 'agent_alice',
      sender_name: 'Alice Analyst',
      content: 'Line of report text. '.repeat(80),
      is_read: true,
      created_at: '2026-07-13 09:00:00',
    });
    try {
      await expandRoom();
      const card = screen.getByTestId('inbox-message-card-msg_long');
      const toggle = card.querySelector('[data-testid="inbox-show-more"]');
      expect(toggle).toBeTruthy();
      const collapsedLabel = toggle!.textContent;
      fireEvent.click(toggle!);
      const expandedLabel = card.querySelector('[data-testid="inbox-show-more"]')!.textContent;
      // The toggle flips between show-more and show-less labels.
      expect(expandedLabel).not.toBe(collapsedLabel);
    } finally {
      ROOMS[0].messages.pop();
    }
  });

  test('short message has no show-more toggle', async () => {
    await expandRoom();
    const card = screen.getByTestId('inbox-message-card-msg_a1');
    expect(card.querySelector('[data-testid="inbox-show-more"]')).toBeNull();
  });

  test('unread messages carry an unread marker', async () => {
    ROOMS[0].messages.push({
      message_id: 'msg_unread',
      sender_id: 'agent_bob',
      sender_name: 'Bob Broker',
      content: 'New unread note.',
      is_read: false,
      created_at: '2026-07-13 09:30:00',
    });
    try {
      await expandRoom();
      const card = screen.getByTestId('inbox-message-card-msg_unread');
      expect(card.querySelector('[data-testid="inbox-unread-dot"]')).toBeTruthy();
      const read = screen.getByTestId('inbox-message-card-msg_a1');
      expect(read.querySelector('[data-testid="inbox-unread-dot"]')).toBeNull();
    } finally {
      ROOMS[0].messages.pop();
    }
  });

  test('member chips show names without raw agent ids', async () => {
    await expandRoom();
    const memberStrip = screen.getByTestId('inbox-member-strip');
    expect(memberStrip).toHaveTextContent('Alice Analyst');
    expect(memberStrip.textContent).not.toContain('agent_alice');
  });

  test('messages from different days get a day separator', async () => {
    ROOMS[0].messages.push({
      message_id: 'msg_older',
      sender_id: 'agent_bob',
      sender_name: 'Bob Broker',
      content: 'From another day.',
      is_read: true,
      created_at: '2026-07-11 10:00:00',
    });
    try {
      await expandRoom();
      expect(screen.getAllByTestId('inbox-day-separator').length).toBeGreaterThanOrEqual(2);
    } finally {
      ROOMS[0].messages.pop();
    }
  });
});
