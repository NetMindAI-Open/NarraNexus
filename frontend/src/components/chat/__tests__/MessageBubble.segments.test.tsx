/**
 * @file_name: MessageBubble.segments.test.tsx
 * @date: 2026-07-30
 * @description: 一轮多次回复 → 气泡内 m 个「说话」，每个带自己那段过程。
 * 后端仍是一轮一条记录；segments 由 stopStreaming（直播）或 event-log
 * fetch（历史）切好挂在消息上。老消息无 segments，回落 content 单段。
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MessageBubble } from '../MessageBubble';
import type { ChatMessage } from '@/types';

const message: ChatMessage = {
  id: 'm1', role: 'assistant', content: '开始了\n\n做完了', timestamp: 0,
  segments: [
    { process: [{ id: 't1', ts: 1, type: 'thinking', content: '先看素材' }],
      reply: { content: '开始了' } },
    { process: [{ id: 'c2', ts: 2, type: 'tool_call', tool_name: 'bash', tool_input: {} }],
      reply: { content: '做完了' } },
  ],
};

describe('MessageBubble segments', () => {
  it('m 段回复各渲染一次（content 不再整块重复渲染）', () => {
    render(<MessageBubble message={message} />);
    expect(screen.getAllByText('开始了')).toHaveLength(1);
    expect(screen.getAllByText('做完了')).toHaveLength(1);
  });

  it('每段各带一个可展开的过程区', () => {
    render(<MessageBubble message={message} />);
    expect(screen.getAllByTestId(/segment-details-/)).toHaveLength(2);
  });

  it('零回复的 segments 回落到 content 单段渲染（现状路径）', () => {
    const noReply: ChatMessage = {
      id: 'm2', role: 'assistant', content: '(Agent decided no response needed)',
      timestamp: 0,
      segments: [
        { process: [{ id: 't1', ts: 1, type: 'thinking', content: '想了想' }], reply: null },
      ],
    };
    render(<MessageBubble message={noReply} />);
    expect(screen.getByText('(Agent decided no response needed)')).toBeInTheDocument();
  });

  it('无 segments 的老消息回落 content 单段渲染', () => {
    const legacy: ChatMessage = { id: 'm3', role: 'assistant', content: '旧消息', timestamp: 0 };
    render(<MessageBubble message={legacy} />);
    expect(screen.getByText('旧消息')).toBeInTheDocument();
  });
});
