/**
 * NMReactFlowConfig unit test — verifies the exported config has the shape
 * that JobsPanel (and any future graph) needs to enforce NM styling.
 */
import { describe, test, expect } from 'vitest';
import {
  nmReactFlowConfig,
  getNMNodeStyle,
  type NMNodeKind,
} from '../reactflow-nm-config';

describe('NMReactFlowConfig', () => {
  test('exports defaultEdgeOptions with NM stroke', () => {
    expect(nmReactFlowConfig.defaultEdgeOptions).toBeDefined();
    expect(nmReactFlowConfig.defaultEdgeOptions.style?.strokeWidth).toBe(1.5);
    expect(nmReactFlowConfig.defaultEdgeOptions.style?.stroke).toBe('rgba(42,38,32,0.50)');
  });

  test('exports species color palette', () => {
    expect(nmReactFlowConfig.speciesColors).toEqual({
      carbon: '#E8704A',
      silicon: '#3D7EC4',
      overlap: '#8E5CB8',
      ink: 'rgba(42,38,32,0.50)',
    });
  });

  test('exports proOptions hiding attribution', () => {
    expect(nmReactFlowConfig.proOptions?.hideAttribution).toBe(true);
  });

  test('exports defaultViewport at origin zoom 1', () => {
    expect(nmReactFlowConfig.defaultViewport).toEqual({ x: 0, y: 0, zoom: 1 });
  });

  test('getNMNodeStyle returns carbon border for user kind', () => {
    const style = getNMNodeStyle('user' as NMNodeKind);
    expect(style.borderColor).toBe('#E8704A');
    expect(style.borderRadius).toBe(10);
    expect(style.background).toBe('var(--nm-card)');
  });

  test('getNMNodeStyle returns silicon border for agent kind', () => {
    const style = getNMNodeStyle('agent' as NMNodeKind);
    expect(style.borderColor).toBe('#3D7EC4');
  });

  test('getNMNodeStyle returns ink-50 border for tool kind', () => {
    const style = getNMNodeStyle('tool' as NMNodeKind);
    expect(style.borderColor).toBe('rgba(42,38,32,0.50)');
  });

  test('getNMNodeStyle returns overlap border for output kind', () => {
    const style = getNMNodeStyle('output' as NMNodeKind);
    expect(style.borderColor).toBe('#8E5CB8');
  });
});
