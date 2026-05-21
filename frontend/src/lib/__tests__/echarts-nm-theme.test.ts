/**
 * NMEChartsTheme unit test — verifies registerNMEChartsTheme() calls
 * echarts.registerTheme with the expected theme names and shape.
 *
 * We mock the echarts module because actually loading echarts in jsdom
 * would pull in a 700KB dependency for a unit test.
 */
import { describe, test, expect, beforeEach, vi } from 'vitest';
import * as echarts from 'echarts';
import { registerNMEChartsTheme, nmEChartsTheme, pickNMTheme } from '../echarts-nm-theme';

vi.mock('echarts', () => ({
  registerTheme: vi.fn(),
}));

describe('NMEChartsTheme', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test('registers nm-light theme', () => {
    registerNMEChartsTheme();
    expect(echarts.registerTheme).toHaveBeenCalledWith(
      'nm-light',
      expect.objectContaining({
        backgroundColor: 'transparent',
        textStyle: expect.objectContaining({
          fontFamily: expect.stringContaining('SF Pro Text'),
        }),
      })
    );
  });

  test('registers nm-dark theme', () => {
    registerNMEChartsTheme();
    expect(echarts.registerTheme).toHaveBeenCalledWith(
      'nm-dark',
      expect.objectContaining({
        backgroundColor: 'transparent',
      })
    );
  });

  test('registerNMEChartsTheme is idempotent (each call re-registers both themes)', () => {
    registerNMEChartsTheme();
    registerNMEChartsTheme();
    expect(echarts.registerTheme).toHaveBeenCalledTimes(4);
  });

  test('exported theme object has species palette (light)', () => {
    expect(nmEChartsTheme.light.species).toEqual({
      carbon: '#E8704A',
      silicon: '#3D7EC4',
      overlap: '#8E5CB8',
    });
  });

  test('exported theme object has species palette (dark, lifted)', () => {
    expect(nmEChartsTheme.dark.species).toEqual({
      carbon: '#FF7A5C',
      silicon: '#7BB1E8',
      overlap: '#B388D9',
    });
  });

  test('default series colors are ink ramp (5 tints)', () => {
    expect(nmEChartsTheme.light.color).toHaveLength(5);
    expect(nmEChartsTheme.light.color[0]).toBe('#2A2620');
  });

  test('pickNMTheme returns nm-light when no .dark class on documentElement', () => {
    document.documentElement.classList.remove('dark');
    expect(pickNMTheme()).toBe('nm-light');
  });

  test('pickNMTheme returns nm-dark when .dark class on documentElement', () => {
    document.documentElement.classList.add('dark');
    expect(pickNMTheme()).toBe('nm-dark');
    document.documentElement.classList.remove('dark');
  });
});
