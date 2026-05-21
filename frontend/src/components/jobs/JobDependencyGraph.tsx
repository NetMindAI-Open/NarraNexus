/**
 * Job Dependency Graph - Visualize job dependencies using React Flow
 *
 * Features:
 * 1. Automatic topological layout (left to right)
 * 2. Node colors change based on status
 * 3. Animated edges for running jobs
 * 4. Click nodes to show details
 */

import { useMemo, useCallback } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  MarkerType,
} from 'reactflow';
import type { Node, Edge } from 'reactflow';
import 'reactflow/dist/style.css';

import type { JobNode, JobNodeStatus } from '@/types/jobComplex';
import { nmReactFlowConfig } from '@/lib/reactflow-nm-config';

interface JobDependencyGraphProps {
  jobs: JobNode[];
  onNodeClick?: (jobId: string) => void;
  selectedJobId?: string | null;
}

// Node color configuration — NM warm-status palette.
// The dependency graph carries status semantics on each node; the
// border color is what the eye lands on first. Backgrounds are kept
// as low-alpha tints of the same hue so the node still reads as
// "warm paper card" not "saturated chip" (Axiom #2).
const statusColors: Record<JobNodeStatus, { bg: string; border: string; text: string }> = {
  pending:   { bg: 'var(--nm-paper-warm)',                        border: 'var(--nm-ink50)',       text: 'var(--nm-ink70)' },
  active:    { bg: 'var(--color-silicon-soft, rgba(61,126,196,0.10))', border: 'var(--color-silicon)', text: 'var(--color-silicon)' },
  running:   { bg: 'rgba(196,154,62,0.12)',                       border: 'var(--color-warning)',  text: 'var(--color-warning)' },
  completed: { bg: 'rgba(107,148,102,0.12)',                      border: 'var(--color-success)',  text: 'var(--color-success)' },
  failed:    { bg: 'rgba(201,90,77,0.12)',                        border: 'var(--color-error)',    text: 'var(--color-error)' },
  cancelled: { bg: 'var(--nm-paper-warm)',                        border: 'var(--nm-ink30)',       text: 'var(--nm-ink50)' },
};

// Status display labels
const statusLabels: Record<JobNodeStatus, string> = {
  pending: 'Pending',
  active: 'Active',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

// Calculate topological level
function getTopologicalLevel(job: JobNode, allJobs: JobNode[], memo: Map<string, number> = new Map()): number {
  if (memo.has(job.id)) {
    return memo.get(job.id)!;
  }

  if (job.depends_on.length === 0) {
    memo.set(job.id, 0);
    return 0;
  }

  const depLevels = job.depends_on
    .map((depKey) => {
      const dep = allJobs.find((j) => j.task_key === depKey || j.id === depKey);
      return dep ? getTopologicalLevel(dep, allJobs, memo) : null;
    })
    .filter((level): level is number => level !== null);

  // If no valid dependencies found, return level 0
  const level = depLevels.length > 0 ? Math.max(...depLevels) + 1 : 0;
  memo.set(job.id, level);
  return level;
}

// Calculate node positions (auto layout)
function calculatePositions(jobs: JobNode[]): Map<string, { x: number; y: number }> {
  const positions = new Map<string, { x: number; y: number }>();
  const memo = new Map<string, number>();

  // Group by level
  const levels: Map<number, JobNode[]> = new Map();
  jobs.forEach((job) => {
    const level = getTopologicalLevel(job, jobs, memo);
    if (!levels.has(level)) {
      levels.set(level, []);
    }
    levels.get(level)!.push(job);
  });

  // Calculate positions
  const horizontalSpacing = 220;
  const verticalSpacing = 100;

  levels.forEach((levelJobs, level) => {
    const startY = -((levelJobs.length - 1) * verticalSpacing) / 2;
    levelJobs.forEach((job, index) => {
      positions.set(job.id, {
        x: level * horizontalSpacing,
        y: startY + index * verticalSpacing,
      });
    });
  });

  return positions;
}

export function JobDependencyGraph({ jobs, onNodeClick, selectedJobId }: JobDependencyGraphProps) {
  // Convert to React Flow format
  const { initialNodes, initialEdges } = useMemo(() => {
    // Safety check: ensure jobs is a valid array
    if (!Array.isArray(jobs) || jobs.length === 0) {
      return { initialNodes: [], initialEdges: [] };
    }

    try {
      const positions = calculatePositions(jobs);

      const nodes: Node[] = jobs.map((job) => {
        const pos = positions.get(job.id) || { x: 0, y: 0 };
        const colors = statusColors[job.status] || statusColors.pending;
        const isSelected = selectedJobId === job.id;

        return {
          id: job.id,
          type: 'default',
          data: {
            label: (
              <div className="text-center px-1">
                <div className="font-medium text-sm truncate" style={{ color: colors.text }}>
                  {job.title || 'Untitled Job'}
                </div>
                <div className="text-xs opacity-75 mt-0.5">
                  {statusLabels[job.status] || job.status}
                </div>
              </div>
            ),
          },
          position: pos,
          style: {
            background: colors.bg,
            border: `2px solid ${isSelected ? 'var(--nm-ink)' : colors.border}`,
            borderRadius: 8,
            padding: '8px 12px',
            minWidth: 120,
            boxShadow: isSelected ? '0 0 0 2px rgba(99, 102, 241, 0.3)' : 'none',
          },
        };
      });

      const edges: Edge[] = jobs.flatMap((job) => {
        if (!job.depends_on || !Array.isArray(job.depends_on)) {
          return [];
        }

        return job.depends_on
          .map((depKey) => {
            const sourceJob = jobs.find((j) => j.task_key === depKey || j.id === depKey);
            if (!sourceJob) return null;

            const isActive = job.status === 'running' || job.status === 'active';

            return {
              id: `${sourceJob.id}-${job.id}`,
              source: sourceJob.id,
              target: job.id,
              animated: isActive,
              style: {
                // NM: active edges in warm ochre (status warning), idle
                // edges in ink-50. No more cool slate (#94a3b8) which
                // didn't fit the warm-paper world.
                stroke: isActive ? 'var(--color-warning)' : 'var(--nm-ink50)',
                strokeWidth: isActive ? 2 : 1,
              },
              markerEnd: {
                type: MarkerType.ArrowClosed,
                color: isActive ? 'var(--color-warning)' : 'var(--nm-ink50)',
              },
            } as Edge;
          })
          .filter((edge) => edge !== null) as Edge[];
      });

      return { initialNodes: nodes, initialEdges: edges };
    } catch (error) {
      console.error('Error calculating graph layout:', error);
      return { initialNodes: [], initialEdges: [] };
    }
  }, [jobs, selectedJobId]);

  const [nodes, , onNodesChange] = useNodesState(initialNodes);
  const [edges, , onEdgesChange] = useEdgesState(initialEdges);

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      onNodeClick?.(node.id);
    },
    [onNodeClick]
  );

  if (jobs.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-[var(--text-tertiary)]">
        No jobs with dependencies
      </div>
    );
  }

  return (
    <div className="w-full h-full min-h-[300px]">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.5}
        maxZoom={2}
        // NM defaults: ink-50 smoothstep edges, hide attribution badge,
        // centered default viewport. Per-node visual identity still
        // owned by custom node renderers below.
        defaultEdgeOptions={nmReactFlowConfig.defaultEdgeOptions}
        proOptions={nmReactFlowConfig.proOptions}
        defaultViewport={nmReactFlowConfig.defaultViewport}
      >
        <Background color="var(--nm-hairline)" gap={16} />
        <Controls showInteractive={false} />
        <MiniMap
          nodeColor={(node) => {
            try {
              const job = jobs.find((j) => j.id === node.id);
              if (job && statusColors[job.status]) {
                return statusColors[job.status].border;
              }
              return nmReactFlowConfig.speciesColors.ink;
            } catch {
              return nmReactFlowConfig.speciesColors.ink;
            }
          }}
          maskColor="var(--nm-backdrop)"
        />
      </ReactFlow>
    </div>
  );
}
