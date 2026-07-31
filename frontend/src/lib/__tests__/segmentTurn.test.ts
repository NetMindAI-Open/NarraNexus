/**
 * segmentTurn — 一轮事件按「用户可见片段」切段。
 * 这些用例锁住四件事：多回复各带自己的过程、末尾残留归最后一段、
 * 零回复不丢过程、连续 native_output 合并。
 */
import { describe, it, expect } from 'vitest';
import { segmentTurn, timelineToEvents } from '../segmentTurn';
import type { EventLogTimelineEntry, TurnEvent } from '@/types';

const think = (id: string, content: string): TurnEvent =>
  ({ id, ts: 0, type: 'thinking', content });
const tool = (id: string, tool_name: string): TurnEvent =>
  ({ id, ts: 0, type: 'tool_call', tool_name, tool_input: {} });
const reply = (id: string, content: string): TurnEvent =>
  ({ id, ts: 0, type: 'reply', content });
const native = (id: string, content: string): TurnEvent =>
  ({ id, ts: 0, type: 'native_output', content });

describe('segmentTurn', () => {
  it('每段回复带上它之前的过程', () => {
    const segs = segmentTurn([
      think('t1', '先看素材'), tool('c1', 'glob'), reply('r1', '开始了'),
      think('t2', '排版'), tool('c2', 'bash'), reply('r2', '做完了'),
    ]);
    expect(segs).toHaveLength(2);
    expect(segs[0].reply?.content).toBe('开始了');
    expect(segs[0].process.map((e) => e.id)).toEqual(['t1', 'c1']);
    expect(segs[1].reply?.content).toBe('做完了');
    expect(segs[1].process.map((e) => e.id)).toEqual(['t2', 'c2']);
  });

  it('最后一次回复之后的过程归到最后一段', () => {
    const segs = segmentTurn([
      reply('r1', '说完了'), think('t9', '再检查一下'), tool('c9', 'read_file'),
    ]);
    expect(segs).toHaveLength(1);
    expect(segs[0].process.map((e) => e.id)).toEqual(['t9', 'c9']);
  });

  it('零回复的轮次产出一个无回复段，过程不丢', () => {
    const segs = segmentTurn([think('t1', '想了想'), tool('c1', 'bash')]);
    expect(segs).toHaveLength(1);
    expect(segs[0].reply).toBeNull();
    expect(segs[0].process.map((e) => e.id)).toEqual(['t1', 'c1']);
  });

  it('连续 native_output 合并成一段，被工具打断则开新段', () => {
    const segs = segmentTurn([
      native('n1', '第一句。'), native('n2', '接着说。'),
      tool('c1', 'bash'), native('n3', '换个话题。'),
    ]);
    expect(segs).toHaveLength(2);
    expect(segs[0].reply?.content).toBe('第一句。接着说。');
    expect(segs[1].reply?.content).toBe('换个话题。');
    expect(segs[1].process.map((e) => e.id)).toEqual(['c1']);
  });

  it('空输入产出空数组', () => {
    expect(segmentTurn([])).toEqual([]);
  });
});

describe('timelineToEvents', () => {
  it('保留 reply，使 event-log 的 timeline 可以直接切段', () => {
    const tl: EventLogTimelineEntry[] = [
      { type: 'thinking', content: '想一下' },
      { type: 'tool_call', tool_name: 'bash', tool_input: { command: 'ls' } },
      { type: 'tool_output', tool_name: 'bash', tool_output: 'a.txt' },
      { type: 'reply', content: '好了', reply_via: 'send_message_to_user_directly' },
    ];
    const events = timelineToEvents(tl);
    expect(events.map((e) => e.type)).toEqual(
      ['thinking', 'tool_call', 'tool_output', 'reply'],
    );
    const segs = segmentTurn(events);
    expect(segs).toHaveLength(1);
    expect(segs[0].reply?.content).toBe('好了');
    expect(segs[0].process).toHaveLength(3);
  });

  it('丢掉内容为空的 thinking / native_output 条目', () => {
    const events = timelineToEvents([
      { type: 'thinking', content: '' },
      { type: 'native_output', content: '' },
      { type: 'thinking', content: '有内容' },
    ]);
    expect(events).toHaveLength(1);
  });

  it('直播事件与 event-log timeline 切出同样的段——这是本设计的核心保证', () => {
    // 同一轮的两种表示：WebSocket 收到的 TurnEvent，和刷新后从
    // /event-log 拿到的 timeline。两者必须切出一致的结果。
    const live: TurnEvent[] = [
      { id: 'a', ts: 0, type: 'thinking', content: '想一下' },
      { id: 'b', ts: 1, type: 'tool_call', tool_name: 'bash', tool_input: { command: 'ls' } },
      { id: 'c', ts: 2, type: 'reply', content: '第一句' },
      { id: 'd', ts: 3, type: 'tool_call', tool_name: 'glob', tool_input: { pattern: '*' } },
      { id: 'e', ts: 4, type: 'reply', content: '第二句' },
    ];
    const reloaded = timelineToEvents([
      { type: 'thinking', content: '想一下' },
      { type: 'tool_call', tool_name: 'bash', tool_input: { command: 'ls' } },
      { type: 'reply', content: '第一句' },
      { type: 'tool_call', tool_name: 'glob', tool_input: { pattern: '*' } },
      { type: 'reply', content: '第二句' },
    ]);

    const shape = (segs: ReturnType<typeof segmentTurn>) =>
      segs.map((s) => ({
        reply: s.reply?.content ?? null,
        process: s.process.map((p) => p.type),
      }));

    expect(shape(segmentTurn(reloaded))).toEqual(shape(segmentTurn(live)));
  });

  it('send_message 的 tool_call 条目转成 reply——持久化 timeline 没有 reply 型别', () => {
    // 后端 /event-log 的 timeline 从不产 type='reply'：回复以
    // send_message_to_user_directly 的 tool_call 形式存储（直播路径
    // chatStore 做同样的转换）。不转换的话，NexusPower 的历史轮次切不出
    // 任何 reply，刷新后整体回落单段——与直播不一致。
    const events = timelineToEvents([
      { type: 'thinking', content: '想一下' },
      { type: 'tool_call',
        tool_name: 'mcp__chat_module__send_message_to_user_directly',
        tool_input: { content: '这就是回复' },
        reply_via: 'helper_llm_no_reply' },
      { type: 'tool_output',
        tool_name: 'mcp__chat_module__send_message_to_user_directly',
        tool_output: 'Message sent' },
    ]);
    expect(events.map((e) => e.type)).toEqual(['thinking', 'reply', 'tool_output']);
    const reply = events[1] as Extract<TurnEvent, { type: 'reply' }>;
    expect(reply.content).toBe('这就是回复');
    expect(reply.reply_via).toBe('helper_llm_no_reply');

    const segs = segmentTurn(events);
    expect(segs).toHaveLength(1);
    expect(segs[0].reply?.content).toBe('这就是回复');
  });
});
