/**
 * @file_name: reactflow-nm-config.ts
 * @author: NM Design System Phase 1 (M1 foundation)
 * @date: 2026-05-18
 * @description: NM-styled defaults for ReactFlow used by JobsPanel and any
 * future graph view. ReactFlow has no theme system, so we centralize the
 * NM-conforming defaults here and pass them as props (or via helper).
 *
 * Spec: reference/self_notebook/specs/2026-05-18-nm-design-system-design.md §6.3
 */

import type { CSSProperties } from 'react';
import type { DefaultEdgeOptions, ProOptions, Viewport } from 'reactflow';

/**
 * Node kind drives the bracket-edge color (Axiom #1: species colors mark
 * identity in the graph). NM JobsPanel uses these four kinds.
 */
export type NMNodeKind = 'user' | 'agent' | 'tool' | 'output';

const SPECIES = {
  carbon: '#E8704A',
  silicon: '#3D7EC4',
  overlap: '#8E5CB8',
  ink: 'rgba(42,38,32,0.50)',
} as const;

const NODE_KIND_TO_COLOR: Record<NMNodeKind, string> = {
  user: SPECIES.carbon,
  agent: SPECIES.silicon,
  tool: SPECIES.ink,
  output: SPECIES.overlap,
};

/**
 * Base style for any NM ReactFlow node. Renderers (custom nodeTypes) should
 * spread this onto their root element, then add per-kind decoration.
 */
export function getNMNodeStyle(kind: NMNodeKind): CSSProperties {
  return {
    background: 'var(--nm-card)',
    border: '1px solid var(--nm-hairline)',
    borderRadius: 10,
    borderColor: NODE_KIND_TO_COLOR[kind],
    padding: '10px 14px',
    fontSize: 13,
    color: 'var(--nm-ink)',
    fontFamily:
      '-apple-system, "SF Pro Text", "PingFang SC", "Noto Sans CJK SC", sans-serif',
    minWidth: 140,
    boxShadow: 'none',
  };
}

export const nmReactFlowConfig: {
  defaultEdgeOptions: DefaultEdgeOptions;
  speciesColors: typeof SPECIES;
  proOptions: ProOptions;
  defaultViewport: Viewport;
} = {
  defaultEdgeOptions: {
    style: {
      stroke: SPECIES.ink,
      strokeWidth: 1.5,
    },
    type: 'smoothstep',
    animated: false,
  },
  speciesColors: SPECIES,
  proOptions: {
    hideAttribution: true,
  },
  defaultViewport: { x: 0, y: 0, zoom: 1 },
};
