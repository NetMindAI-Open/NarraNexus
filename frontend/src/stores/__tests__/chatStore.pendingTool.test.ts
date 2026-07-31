/**
 * 工具「一检测到就显示」：名字先到（pending），参数齐了覆盖同一条。
 *
 * 为什么必须原地替换而不是各记一条：`currentToolCalls` 是回复提取的数据源
 * （stopStreaming 从里面挑 send_message_to_user_directly 的 content），
 * 一条参数为空的重复条目会注入一段空回复。
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { useChatStore } from '../chatStore';
import type { ProgressMessage } from '@/types';

const AGENT = 'agent_pending_tool';

const toolProgress = (pending: boolean): ProgressMessage => ({
  type: 'progress',
  step: '3.4.1',
  title: 'tool',
  description: '',
  status: 'running',
  substeps: [],
  timestamp: pending ? 1000 : 2000,
  details: {
    tool_name: 'bash',
    tool_call_id: 'call_1',
    arguments: pending ? {} : { command: 'ls -la' },
    pending,
  },
});

describe('chatStore pending tool_call', () => {
  beforeEach(() => {
    useChatStore.getState().clearAgent(AGENT);
    useChatStore.getState().startStreaming(AGENT);
  });

  it('pending 事件先出现，带名字、参数为空', () => {
    useChatStore.getState().processMessage(AGENT, toolProgress(true));
    const events = useChatStore.getState().agentSessions[AGENT].currentEvents;
    const call = events.find((e) => e.type === 'tool_call');
    expect(call).toMatchObject({ tool_name: 'bash', pending: true });
  });

  it('完整事件按 tool_call_id 原地替换，事件与工具表都只留一条', () => {
    const store = useChatStore.getState();
    store.processMessage(AGENT, toolProgress(true));
    store.processMessage(AGENT, toolProgress(false));

    const session = useChatStore.getState().agentSessions[AGENT];
    const calls = session.currentEvents.filter((e) => e.type === 'tool_call');
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ pending: false, tool_input: { command: 'ls -la' } });
    expect(session.currentToolCalls).toHaveLength(1);
  });
});
