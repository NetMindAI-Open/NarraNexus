/**
 * @file_name: officeEditCommands.test.ts
 * @description: The T1 office edit vocabulary (spec B §3.3), as verified
 * against officecli 1.0.144 on 2026-08-19: batch items are
 * {command, path, props?} and the watch page reports selections as
 * {paths: [...]} — these builders/parsers are the single place that
 * encodes both facts.
 */

import { describe, expect, it } from 'vitest';
import {
  buildRemoveCommands,
  buildSetPropsCommands,
  buildSetTextCommand,
  parseSelectionMessage,
} from '../officeEditCommands';

describe('command builders', () => {
  it('remove: one command per selected path', () => {
    expect(buildRemoveCommands(['/slide[1]/pic[1]', '/slide[1]/sp[2]'])).toEqual([
      { command: 'remove', path: '/slide[1]/pic[1]' },
      { command: 'remove', path: '/slide[1]/sp[2]' },
    ]);
  });

  it('set props: bold/color per path', () => {
    expect(buildSetPropsCommands(['/body/p[1]'], { bold: true, color: 'FF0000' })).toEqual([
      { command: 'set', path: '/body/p[1]', props: { bold: true, color: 'FF0000' } },
    ]);
  });

  it('set text targets exactly one path', () => {
    expect(buildSetTextCommand('/Sheet1/row[2]/c[3]', 'new value')).toEqual({
      command: 'set',
      path: '/Sheet1/row[2]/c[3]',
      props: { text: 'new value' },
    });
  });
});

describe('parseSelectionMessage', () => {
  it('parses the watch page selection body', () => {
    expect(parseSelectionMessage('{"paths":["/body/p[1]","/body/p[2]"]}')).toEqual([
      '/body/p[1]',
      '/body/p[2]',
    ]);
  });

  it('tolerates a bare array body', () => {
    expect(parseSelectionMessage('["/body/p[1]"]')).toEqual(['/body/p[1]']);
  });

  it('returns empty on garbage', () => {
    expect(parseSelectionMessage('not json')).toEqual([]);
    expect(parseSelectionMessage('{"other":1}')).toEqual([]);
    expect(parseSelectionMessage('{"paths":"nope"}')).toEqual([]);
  });
});

describe('T2 builders and path classifiers', () => {
  it('slide move command', async () => {
    const m = await import('../officeEditCommands');
    expect(m.buildMoveCommand('/slide[3]', 1)).toEqual({
      command: 'move', path: '/slide[3]', index: 1,
    });
  });

  it('add row/column commands carry parent and optional index', async () => {
    const m = await import('../officeEditCommands');
    expect(m.buildAddCommand('/Sheet1', 'row', 2)).toEqual({
      command: 'add', parent: '/Sheet1', type: 'row', index: 2,
    });
    expect(m.buildAddCommand('/Sheet1', 'column')).toEqual({
      command: 'add', parent: '/Sheet1', type: 'column',
    });
  });

  it('formula set uses the formula prop', async () => {
    const m = await import('../officeEditCommands');
    expect(m.buildSetFormulaCommand('/Sheet1/B2', '=SUM(A1:A9)')).toEqual({
      command: 'set', path: '/Sheet1/B2', props: { formula: '=SUM(A1:A9)' },
    });
  });

  it('image src replace', async () => {
    const m = await import('../officeEditCommands');
    expect(m.buildSetSrcCommand('/slide[1]/pic[2]', '/abs/new.png')).toEqual({
      command: 'set', path: '/slide[1]/pic[2]', props: { src: '/abs/new.png' },
    });
  });

  it('classifies slide paths', async () => {
    const m = await import('../officeEditCommands');
    expect(m.slideIndexFromPath('/slide[3]')).toBe(3);
    expect(m.slideIndexFromPath('/slide[3]/sp[1]')).toBeNull();
    expect(m.slideIndexFromPath('/body/p[1]')).toBeNull();
  });

  it('classifies cell paths into sheet + row', async () => {
    const m = await import('../officeEditCommands');
    expect(m.cellFromPath('/Sheet1/B12')).toEqual({ sheet: '/Sheet1', row: 12 });
    expect(m.cellFromPath('/Q3 数据/AA3')).toEqual({ sheet: '/Q3 数据', row: 3 });
    expect(m.cellFromPath('/slide[1]/pic[1]')).toBeNull();
    expect(m.cellFromPath('/body/p[2]')).toBeNull();
  });

  it('classifies picture paths', async () => {
    const m = await import('../officeEditCommands');
    expect(m.isPicturePath('/slide[1]/pic[2]')).toBe(true);
    expect(m.isPicturePath('/slide[1]/picture[@id=100001]')).toBe(true);
    expect(m.isPicturePath('/slide[1]/sp[1]')).toBe(false);
  });
});
