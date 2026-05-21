/**
 * @file_name: echarts-nm-theme.ts
 * @author: NM Design System Phase 1 (M1 foundation)
 * @date: 2026-05-18
 * @description: Register ECharts themes 'nm-light' / 'nm-dark' that match the
 * NM Design System (Carbon × Silicon, warm paper, system fonts).
 *
 * Usage:
 *   import './lib/echarts-nm-theme';        // side-effect: themes are registered
 *   // or
 *   import { registerNMEChartsTheme } from './lib/echarts-nm-theme';
 *   registerNMEChartsTheme();
 *
 * Then pass theme name to echarts.init():
 *   const chart = echarts.init(dom, pickNMTheme());
 *
 * Spec: reference/self_notebook/specs/2026-05-18-nm-design-system-design.md §6.2
 */

import * as echarts from 'echarts';

const FONT_STACK =
  '-apple-system, "SF Pro Text", "PingFang SC", "Noto Sans CJK SC", "Helvetica Neue", Arial, sans-serif';

export const nmEChartsTheme = {
  light: {
    backgroundColor: 'transparent',
    // Default series colors = ink ramp (5 tints). Use species via opt-in.
    color: ['#2A2620', '#5C5852', '#8B8680', '#B5B0A8', '#D6D1C9'],
    textStyle: {
      fontFamily: FONT_STACK,
      color: '#2A2620',
    },
    title: {
      textStyle: { color: '#2A2620', fontWeight: 700 },
      subtextStyle: { color: 'rgba(42,38,32,0.50)' },
    },
    axisLine: { lineStyle: { color: 'rgba(42,38,32,0.22)' } },
    axisTick: { lineStyle: { color: 'rgba(42,38,32,0.22)' } },
    axisLabel: { color: 'rgba(42,38,32,0.50)', fontFamily: FONT_STACK },
    splitLine: { lineStyle: { color: 'rgba(42,38,32,0.08)' } },
    splitArea: { areaStyle: { color: ['rgba(42,38,32,0.02)', 'transparent'] } },
    legend: {
      textStyle: { color: 'rgba(42,38,32,0.70)', fontFamily: FONT_STACK },
    },
    tooltip: {
      backgroundColor: '#F5F2EB',
      borderColor: 'rgba(42,38,32,0.14)',
      borderWidth: 1,
      textStyle: { color: '#2A2620', fontFamily: FONT_STACK },
      extraCssText:
        'border-radius: 10px; box-shadow: 0 1px 0 rgba(42,38,32,0.04), 0 2px 6px rgba(42,38,32,0.05);',
    },
    // NM species palette (use explicitly when distinguishing human/AI)
    species: {
      carbon: '#E8704A',
      silicon: '#3D7EC4',
      overlap: '#8E5CB8',
    },
  },
  dark: {
    backgroundColor: 'transparent',
    color: ['#F2EFE8', '#C8C2B6', '#9A9388', '#6B655A', '#4A4540'],
    textStyle: {
      fontFamily: FONT_STACK,
      color: '#F2EFE8',
    },
    title: {
      textStyle: { color: '#F2EFE8', fontWeight: 700 },
      subtextStyle: { color: 'rgba(242,239,232,0.50)' },
    },
    axisLine: { lineStyle: { color: 'rgba(242,239,232,0.28)' } },
    axisTick: { lineStyle: { color: 'rgba(242,239,232,0.28)' } },
    axisLabel: { color: 'rgba(242,239,232,0.50)', fontFamily: FONT_STACK },
    splitLine: { lineStyle: { color: 'rgba(242,239,232,0.10)' } },
    splitArea: { areaStyle: { color: ['rgba(242,239,232,0.02)', 'transparent'] } },
    legend: {
      textStyle: { color: 'rgba(242,239,232,0.72)', fontFamily: FONT_STACK },
    },
    tooltip: {
      backgroundColor: '#2A241D',
      borderColor: 'rgba(242,239,232,0.18)',
      borderWidth: 1,
      textStyle: { color: '#F2EFE8', fontFamily: FONT_STACK },
      extraCssText:
        'border-radius: 10px; box-shadow: 0 1px 0 rgba(0,0,0,0.18), 0 2px 6px rgba(0,0,0,0.30);',
    },
    species: {
      carbon: '#FF7A5C',
      silicon: '#7BB1E8',
      overlap: '#B388D9',
    },
  },
};

/**
 * Register both themes with the global echarts module. Safe to call multiple
 * times — each call re-registers (echarts.registerTheme is idempotent by name).
 */
export function registerNMEChartsTheme(): void {
  echarts.registerTheme('nm-light', nmEChartsTheme.light);
  echarts.registerTheme('nm-dark', nmEChartsTheme.dark);
}

/**
 * Pick the right theme name based on current dark-mode state.
 * Useful when initializing a chart that needs to react to live theme changes.
 */
export function pickNMTheme(): 'nm-light' | 'nm-dark' {
  if (typeof document === 'undefined') return 'nm-light';
  return document.documentElement.classList.contains('dark') ? 'nm-dark' : 'nm-light';
}

// Auto-register on import so consumers can simply `import './lib/echarts-nm-theme';`
registerNMEChartsTheme();
