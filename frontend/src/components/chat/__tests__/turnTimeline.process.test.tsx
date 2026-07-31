/**
 * 分工确立后：TurnTimeline 只渲染过程。
 *
 * 答案层已经由气泡（SegmentedReply）负责；若时间线还渲染 reply /
 * native_output，同一句话会在气泡和折叠区各出现一次。
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TurnTimeline } from '../TurnTimeline';
import type { TurnEvent } from '@/types';

describe('TurnTimeline 只渲染过程', () => {
  it('不渲染 reply 与 native_output', () => {
    const events: TurnEvent[] = [
      { id: 't1', ts: 1, type: 'thinking', content: '思考内容' },
      { id: 'r1', ts: 2, type: 'reply', content: '这是回复不该出现' },
      { id: 'n1', ts: 3, type: 'native_output', content: '这是原生输出不该出现' },
    ];
    render(<TurnTimeline events={events} />);
    expect(screen.getByText(/思考内容/)).toBeInTheDocument();
    expect(screen.queryByText(/这是回复不该出现/)).toBeNull();
    expect(screen.queryByText(/这是原生输出不该出现/)).toBeNull();
  });

  it('只有答案层事件时整体不渲染', () => {
    const { container } = render(
      <TurnTimeline events={[{ id: 'r1', ts: 1, type: 'reply', content: '只有回复' }]} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('plan 也不在这里渲染——它归 ProcessPanel 底部固定区', () => {
    render(
      <TurnTimeline
        events={[
          { id: 't1', ts: 1, type: 'thinking', content: '思考内容' },
          { id: 'p1', ts: 2, type: 'plan', steps: [{ step: '某步骤', status: 'pending' }] },
        ]}
      />,
    );
    expect(screen.queryByText('某步骤')).toBeNull();
  });
});
