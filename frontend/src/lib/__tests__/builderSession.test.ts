/**
 * The studio flag must be READ-ONLY on read (the instruction rides along on
 * every turn, so a read that consumed it would silently stop the draft block
 * after the first message), reactive (components subscribe to it), and
 * persisted per tab (a reload lands back in the studio).
 */
import { describe, test, expect, beforeEach } from 'vitest';
import { loadStudioSession } from '../builderSession';
import { useStudioStore, isStudioOpen, selectRecommendations, selectStudioResumable } from '@/stores/studioStore';

beforeEach(() => {
  window.sessionStorage.clear();
  useStudioStore.setState({ open: {}, visited: {}, recommendations: {}, applyError: {} });
});

const store = () => useStudioStore.getState();

describe('studio flag', () => {
  test('an untouched agent is not in the studio', () => {
    expect(isStudioOpen('agt_1')).toBe(false);
  });

  test('reading does not consume — every turn must still be wrapped', () => {
    store().openStudio('agt_1');
    expect(isStudioOpen('agt_1')).toBe(true);
    expect(isStudioOpen('agt_1')).toBe(true);
    expect(isStudioOpen('agt_1')).toBe(true);
  });

  test('close COLLAPSES: flag and error go, recommendations stay, and it is resumable', () => {
    // The drawer's X must not be an unlabeled, irreversible "end the AI
    // creation flow" button — collapsing keeps the suggestions and the way back.
    store().openStudio('agt_1');
    store().setRecommendations('agt_1', { skill_ids: ['web-search'], channels: [] });
    store().setApplyError('agt_1', 'boom');
    store().closeStudio('agt_1');
    expect(isStudioOpen('agt_1')).toBe(false);
    expect(store().applyError['agt_1']).toBeUndefined();
    expect(selectRecommendations('agt_1')(store()).skill_ids).toEqual(['web-search']);
    expect(selectStudioResumable('agt_1')(store())).toBe(true);
    // and resuming brings it back with the suggestions intact
    store().openStudio('agt_1');
    expect(isStudioOpen('agt_1')).toBe(true);
    expect(selectStudioResumable('agt_1')(store())).toBe(false);
    expect(selectRecommendations('agt_1')(store()).skill_ids).toEqual(['web-search']);
  });

  test('finish ENDS: flag, resumability, recommendations and error all go', () => {
    store().openStudio('agt_1');
    store().setRecommendations('agt_1', { skill_ids: ['web-search'], channels: [] });
    store().setApplyError('agt_1', 'boom');
    store().finishStudio('agt_1');
    expect(isStudioOpen('agt_1')).toBe(false);
    expect(selectStudioResumable('agt_1')(store())).toBe(false);
    expect(selectRecommendations('agt_1')(store()).skill_ids).toEqual([]);
    expect(store().applyError['agt_1']).toBeUndefined();
    expect(window.sessionStorage.length).toBe(0);
  });

  test('an agent that never entered the studio is not resumable', () => {
    expect(selectStudioResumable('agt_9')(store())).toBe(false);
  });

  test('the flag is per agent', () => {
    store().openStudio('agt_1');
    expect(isStudioOpen('agt_2')).toBe(false);
  });

  test('closing one agent does not close another', () => {
    store().openStudio('agt_1');
    store().openStudio('agt_2');
    store().closeStudio('agt_1');
    expect(isStudioOpen('agt_1')).toBe(false);
    expect(isStudioOpen('agt_2')).toBe(true);
  });

  test('a blank or absent agent id is inert in every direction', () => {
    store().openStudio('');
    expect(isStudioOpen('')).toBe(false);
    expect(isStudioOpen(null)).toBe(false);
    expect(isStudioOpen(undefined)).toBe(false);
    expect(() => store().closeStudio('')).not.toThrow();
    expect(() => store().finishStudio('')).not.toThrow();
    expect(window.sessionStorage.length).toBe(0);
  });

  test('changes notify subscribers — the panel and the encoder both watch it', () => {
    const seen: boolean[] = [];
    const unsub = useStudioStore.subscribe((s) => seen.push(s.open['agt_1'] === true));
    store().openStudio('agt_1');
    store().closeStudio('agt_1');
    unsub();
    expect(seen).toEqual([true, false]);
  });
});

describe('persistence', () => {
  test('flag, visited marker and recommendations survive a fresh hydration of this tab', () => {
    store().openStudio('agt_1');
    store().setRecommendations('agt_1', { skill_ids: ['web-search'], channels: ['telegram'] });
    store().openStudio('agt_2');
    store().closeStudio('agt_2');
    const again = loadStudioSession();
    expect(again.open).toEqual({ agt_1: true });
    expect(again.visited).toEqual({ agt_1: true, agt_2: true });
    expect(again.recommendations['agt_1']).toEqual({ skill_ids: ['web-search'], channels: ['telegram'] });
  });

  test('a corrupt recommendations entry is dropped rather than throwing', () => {
    window.sessionStorage.setItem('nn.studioRec.agt_9', '{not json');
    window.sessionStorage.setItem('nn.studioRec.agt_8', '{"skill_ids":["ok",3],"channels":"x"}');
    const again = loadStudioSession();
    expect(again.recommendations['agt_9']).toBeUndefined();
    expect(again.recommendations['agt_8']).toEqual({ skill_ids: ['ok'], channels: [] });
  });
});
