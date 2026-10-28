/**
 * @file_name: when.test.ts
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: The `when` grammar — three clause kinds, negation, AND, and a typo is an error rather than "always visible".
 */
import { describe, expect, it } from 'vitest';

import { evaluateWhen, parseWhen } from '@/platform/registries/when';

const ctx = { conversationKind: 'team', agentModules: ['JobModule'], settings: { fastMode: true, narrationTier: 0 } };

describe('when predicates', () => {
  it('evaluates each clause kind and negation', () => {
    expect(evaluateWhen('conversationKind:team', ctx)).toBe(true);
    expect(evaluateWhen('conversationKind:chat', ctx)).toBe(false);
    expect(evaluateWhen('!conversationKind:chat', ctx)).toBe(true);
    expect(evaluateWhen('agentHas:JobModule', ctx)).toBe(true);
    expect(evaluateWhen('agentHas:LarkModule', ctx)).toBe(false);
    expect(evaluateWhen('setting:fastMode', ctx)).toBe(true);
    expect(evaluateWhen('setting:narrationTier', ctx)).toBe(false);
    expect(evaluateWhen('!setting:missing', ctx)).toBe(true);
  });

  it('ANDs a list and treats no clause as always visible', () => {
    expect(evaluateWhen(['conversationKind:team', 'agentHas:JobModule'], ctx)).toBe(true);
    expect(evaluateWhen(['conversationKind:team', 'agentHas:Nope'], ctx)).toBe(false);
    expect(evaluateWhen(undefined, {})).toBe(true);
    expect(evaluateWhen([], {})).toBe(true);
  });

  it('rejects unknown clause kinds instead of ignoring them', () => {
    expect(() => parseWhen('visible:always')).toThrow(/invalid when clause/);
    expect(() => parseWhen('conversationKind')).toThrow(/invalid when clause/);
  });
});
