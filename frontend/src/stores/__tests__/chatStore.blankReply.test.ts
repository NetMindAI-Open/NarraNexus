/**
 * Blank replies never become bubbles: when send_message_to_user_directly
 * carries whitespace-only content ("\n"), stopStreaming falls through to
 * the placeholder branch — the same line the backend hook_persist_turn
 * strip guard draws. Otherwise the session shows a blank bubble that
 * vanishes on refresh (the DB side is caught by the strip guard, the
 * session side let it through via filter(Boolean)).
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { useChatStore } from '../chatStore';
import type { ProgressMessage } from '@/types';

const AGENT = 'agent_blank_reply';

const replyProgress = (content: string, id: string, ts = 1000): ProgressMessage => ({
  type: 'progress',
  step: '3.4.1',
  title: 'send_message_to_user_directly',
  description: '',
  status: 'completed',
  substeps: [],
  // Distinct timestamps: currentToolCalls dedups on tool_name+timestamp.
  timestamp: ts,
  details: {
    tool_name: 'mcp__chat_module__send_message_to_user_directly',
    tool_call_id: id,
    arguments: { content },
  },
});

describe('chatStore blank reply guard', () => {
  beforeEach(() => {
    useChatStore.getState().clearAgent(AGENT);
    useChatStore.getState().startStreaming(AGENT);
  });

  it('stopStreaming: whitespace-only reply falls through to the placeholder', () => {
    useChatStore.getState().processMessage(AGENT, replyProgress('\n', 'c1'));
    useChatStore.getState().stopStreaming(AGENT);
    const messages = useChatStore.getState().agentSessions[AGENT].messages;
    const last = messages[messages.length - 1];
    expect(last.role).toBe('assistant');
    expect(last.content).toBe('(Agent decided no response needed)');
  });

  it('stopStreaming: whitespace parts are dropped, real reply survives', () => {
    useChatStore.getState().processMessage(AGENT, replyProgress('   ', 'c1'));
    useChatStore.getState().processMessage(AGENT, replyProgress('real reply', 'c2', 2000));
    useChatStore.getState().stopStreaming(AGENT);
    const messages = useChatStore.getState().agentSessions[AGENT].messages;
    expect(messages[messages.length - 1].content).toBe('real reply');
  });
});
