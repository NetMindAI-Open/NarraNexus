/**
 * ProcessPanel — 运行中的过程面板。钉住 v3 契约：只渲染过程事件、
 * pipeline 阶段行收进面板、plan 固定在底部、pending 工具有进行中标记、
 * 可折叠（折叠态 = 当前活动 + plan 进度 1-2 行）、空数据也渲染启动态。
 */
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ProcessPanel } from '../ProcessPanel';
import type { TurnEvent, Step } from '@/types';

const events: TurnEvent[] = [
  { id: 't1', ts: 1, type: 'thinking', content: '正在读取需求' },
  { id: 'c1', ts: 2, type: 'tool_call', tool_name: 'glob', tool_input: { pattern: '**/*.md' } },
  { id: 'c2', ts: 3, type: 'tool_call', tool_name: 'register_artifact', tool_input: {}, pending: true },
  { id: 'r1', ts: 4, type: 'reply', content: '这句话属于气泡，不该出现在面板里' },
  { id: 'p1', ts: 5, type: 'plan', steps: [
      { step: '建目录', status: 'completed' },
      { step: '写正文', status: 'in_progress' },
      { step: '注册', status: 'pending' },
    ] },
];

function step(id: string, status: Step['status'] = 'running'): Step {
  return {
    id: `s-${id}`, step: id, title: `Step ${id}`, description: '',
    status, substeps: [], timestamp: 0,
  };
}

describe('ProcessPanel', () => {
  it('渲染思考与工具，不渲染回复', () => {
    render(<ProcessPanel events={events} />);
    expect(screen.getByText(/正在读取需求/)).toBeInTheDocument();
    expect(screen.getByText('glob')).toBeInTheDocument();
    expect(screen.queryByText(/这句话属于气泡/)).toBeNull();
  });

  it('pending 工具显示名字并标记进行中', () => {
    render(<ProcessPanel events={events} />);
    const row = screen.getByTestId('tool-row-c2');
    expect(row).toHaveTextContent('register_artifact');
    expect(row).toHaveAttribute('data-pending', 'true');
  });

  it('参数齐了的工具不带进行中标记', () => {
    render(<ProcessPanel events={events} />);
    expect(screen.getByTestId('tool-row-c1')).toHaveAttribute('data-pending', 'false');
  });

  it('plan 渲染在底部固定区，含三个步骤', () => {
    render(<ProcessPanel events={events} />);
    const plan = screen.getByTestId('process-plan');
    expect(plan).toHaveTextContent('建目录');
    expect(plan).toHaveTextContent('写正文');
    expect(plan).toHaveTextContent('注册');
  });

  it('plan 多次更新时只渲染最后一份快照', () => {
    const withTwoPlans: TurnEvent[] = [
      ...events,
      { id: 'p2', ts: 6, type: 'plan', steps: [{ step: '收尾', status: 'in_progress' }] },
    ];
    render(<ProcessPanel events={withTwoPlans} />);
    const plan = screen.getByTestId('process-plan');
    expect(plan).toHaveTextContent('收尾');
    expect(plan).not.toHaveTextContent('建目录');
  });

  // v3: the panel replaces the message-area "starting up…" indicator, so
  // it must render (with the starting status) even before any data.
  it('空数据渲染启动态而不是消失', () => {
    render(<ProcessPanel events={[]} />);
    expect(screen.getByTestId('process-panel')).toBeInTheDocument();
    expect(screen.getByText(/Starting up/)).toBeInTheDocument();
  });

  it('pipeline 阶段行收进面板（加载上下文/构建上下文）', () => {
    render(<ProcessPanel events={[]} steps={[step('1', 'completed'), step('3')]} />);
    expect(screen.getByText(/Loading context/)).toBeInTheDocument();
    expect(screen.getByText(/Building context/)).toBeInTheDocument();
  });

  it('工具子步骤（3.4.x）不重复出现在阶段行里', () => {
    render(<ProcessPanel events={events} steps={[step('3'), step('3.4.1')]} />);
    expect(screen.queryByText('Step 3.4.1')).toBeNull();
  });

  describe('折叠', () => {
    it('点击头部折叠：过程体隐藏，显示当前活动行', () => {
      render(<ProcessPanel events={events} />);
      fireEvent.click(screen.getByTestId('process-panel-header'));
      // Body rows gone…
      expect(screen.queryByTestId('tool-row-c1')).toBeNull();
      // …one activity line: the latest pending tool is what's happening now.
      const activity = screen.getByTestId('process-activity');
      expect(activity).toHaveTextContent('register_artifact');
    });

    it('折叠态有 plan 时显示进展行，无 plan 时不显示', () => {
      render(<ProcessPanel events={events} />);
      fireEvent.click(screen.getByTestId('process-panel-header'));
      expect(screen.getByTestId('process-plan-mini')).toHaveTextContent('1/3');
      expect(screen.getByTestId('process-plan-mini')).toHaveTextContent('写正文');
    });

    it('折叠态无 plan：进展行不渲染', () => {
      const noPlan = events.filter((e) => e.type !== 'plan');
      render(<ProcessPanel events={noPlan} />);
      fireEvent.click(screen.getByTestId('process-panel-header'));
      expect(screen.queryByTestId('process-plan-mini')).toBeNull();
    });

    it('再点一次展开回来', () => {
      render(<ProcessPanel events={events} />);
      fireEvent.click(screen.getByTestId('process-panel-header'));
      fireEvent.click(screen.getByTestId('process-panel-header'));
      expect(screen.getByTestId('tool-row-c1')).toBeInTheDocument();
    });
  });
});
