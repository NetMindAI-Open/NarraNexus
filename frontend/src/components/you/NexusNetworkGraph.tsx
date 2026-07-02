/**
 * @file_name: NexusNetworkGraph.tsx
 * @author:
 * @date: 2026-06-23
 * @description: The Nexus Network tab of the "You" workspace — a real-data
 * radial graph of everyone the user's agents know, MERGED across agents.
 *
 * You sit at the centre (carbon). Each entity your agents know radiates out,
 * coloured by kind (people=carbon, agents=silicon, groups=purple); distance =
 * familiarity (direct closer, known_of farther); node size + edge weight grow
 * with how many of your agents know them (the cross-agent signal). Clicking a
 * node shows who knows them and what they are. Data: api.getMyNetwork() →
 * GET /api/me/network. Owner-scoped: never reads the selected agentId.
 */
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '@/lib/api';
import { useConfigStore } from '@/stores/configStore';
import type { MyNetworkEntity } from '@/types';
import { BracketEmptyState } from '@/components/nm';

type LoadState =
  | { phase: 'loading' }
  | { phase: 'error'; message: string }
  | { phase: 'ready'; items: MyNetworkEntity[] };

const TYPE_COLOR: Record<string, string> = {
  user: 'var(--color-carbon)',
  agent: 'var(--color-silicon)',
  group: '#8E5CB8',
};
const typeColor = (t: string) => TYPE_COLOR[t] ?? 'var(--color-silicon)';

const fmtDay = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' });

// SVG canvas + radial constants.
const W = 640;
const H = 400;
const CX = W / 2;
const CY = H / 2;
const R_DIRECT = 112;
const R_KNOWN = 168;

export function NexusNetworkGraph({ search = '' }: { search?: string }) {
  const { t } = useTranslation();
  const displayName = useConfigStore((s) => s.displayName);
  const userId = useConfigStore((s) => s.userId);
  const youName = (displayName || userId || t('you.network.youDefault')).trim();

  const [state, setState] = useState<LoadState>({ phase: 'loading' });
  const [selected, setSelected] = useState<string | null>(null);
  const q = search.trim().toLowerCase();

  useEffect(() => {
    let alive = true;
    api
      .getMyNetwork()
      .then((res) => {
        if (!alive) return;
        if (res.success) setState({ phase: 'ready', items: res.entities });
        else setState({ phase: 'error', message: res.error || t('you.common.failedToLoad') });
      })
      .catch((e: unknown) => {
        if (alive) setState({ phase: 'error', message: e instanceof Error ? e.message : String(e) });
      });
    return () => {
      alive = false;
    };
    // Load once on mount; `t` is referentially stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const graph = useMemo(() => {
    if (state.phase !== 'ready') return null;
    // The user's own entity is the centre, not an outer node. A search query
    // filters the outer nodes by name / description / which agents know them.
    const outer = state.items
      .filter((e) => !e.is_self)
      .filter((e) =>
        !q ||
        e.name.toLowerCase().includes(q) ||
        e.description.toLowerCase().includes(q) ||
        e.known_by.some((a) => a.toLowerCase().includes(q)) ||
        e.expertise_domains.some((d) => d.toLowerCase().includes(q)),
      );
    if (outer.length === 0) return { outer: [], nodes: [] as PlacedNode[] };

    const maxKnown = Math.max(...outer.map((e) => e.known_by.length), 1);
    const nodes: PlacedNode[] = outer.map((e, i) => {
      // Spread evenly around the circle, starting at the top.
      const ang = (-90 + (360 / outer.length) * i) * (Math.PI / 180);
      const r = e.familiarity === 'direct' ? R_DIRECT : R_KNOWN;
      const size = Math.min(13 + e.known_by.length * 4 + Math.min(e.interactions, 30) * 0.22, 30);
      const edge = 1 + (e.known_by.length / maxKnown) * 3;
      return {
        e,
        x: CX + r * Math.cos(ang),
        y: CY + r * Math.sin(ang),
        size,
        edge,
      };
    });
    return { outer, nodes };
  }, [state, q]);

  if (state.phase === 'loading') {
    return (
      <Center>
        <span className="text-[12px] font-[family-name:var(--font-mono)] uppercase tracking-[0.12em] text-[var(--text-tertiary)] animate-pulse">
          {t('you.network.loading')}
        </span>
      </Center>
    );
  }
  if (state.phase === 'error') {
    return (
      <Center>
        <BracketEmptyState label={t('you.network.errorLabel')} hint={state.message} />
      </Center>
    );
  }
  if (!graph || graph.nodes.length === 0) {
    return (
      <Center>
        <BracketEmptyState
          label={q ? t('you.network.noMatches') : t('you.network.emptyLabel')}
          hint={
            q
              ? t('you.network.noMatchesHint', { query: search.trim() })
              : t('you.network.emptyHint')
          }
        />
      </Center>
    );
  }

  const selectedItem = graph.outer.find((e) => e.key === selected) ?? null;

  return (
    <div className="w-full h-full flex flex-col p-4">
      {/* legend */}
      <div className="flex items-center gap-4 mb-1 shrink-0 px-1">
        {[
          [t('you.network.legendPeople'), 'var(--color-carbon)'],
          [t('you.network.legendAgents'), 'var(--color-silicon)'],
          [t('you.network.legendGroups'), '#8E5CB8'],
        ].map(([label, color]) => (
          <span
            key={label}
            className="inline-flex items-center gap-1.5 text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.12em] text-[var(--text-tertiary)]"
          >
            <span className="w-2 h-2 rounded-full allow-circle" style={{ background: color }} />
            {label}
          </span>
        ))}
        <span className="ml-auto text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.1em] text-[var(--text-tertiary)]">
          {t('you.network.legendHint')}
        </span>
      </div>

      {/* graph */}
      <div className="flex-1 min-h-0">
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" height="100%" preserveAspectRatio="xMidYMid meet">
          <style>{`
            .nx-node{cursor:pointer}
            .nx-node .nx-hit{opacity:0}
            .nx-node:hover .nx-glow{opacity:.9}
            .nx-node:focus{outline:none}
            @keyframes nxspin{to{transform:rotate(360deg)}}
            .nx-spin{animation:nxspin 100s linear infinite;transform-origin:${CX}px ${CY}px;transform-box:view-box}
            .nx-unspin{animation:nxspin 100s linear infinite reverse;transform-origin:center;transform-box:fill-box}
            @media (prefers-reduced-motion: reduce){.nx-spin,.nx-unspin{animation:none}}
          `}</style>

          {/* concentric familiarity rings — distance from you = closeness */}
          <circle cx={CX} cy={CY} r={R_DIRECT} fill="none" stroke="var(--text-tertiary)" strokeOpacity={0.7} strokeDasharray="3 4" />
          <circle cx={CX} cy={CY} r={R_KNOWN} fill="none" stroke="var(--text-tertiary)" strokeOpacity={0.7} strokeDasharray="3 4" />
          <text
            x={CX - R_DIRECT}
            y={CY - 5}
            textAnchor="middle"
            fontSize={9}
            fontFamily="var(--font-mono)"
            fill="var(--text-tertiary)"
          >
            {t('you.network.ringDirect')}
          </text>
          <text
            x={CX - R_KNOWN}
            y={CY - 5}
            textAnchor="middle"
            fontSize={9}
            fontFamily="var(--font-mono)"
            fill="var(--text-tertiary)"
          >
            {t('you.network.ringKnownOf')}
          </text>

          {/* Rotating layer — the entities slowly orbit you; the rings,
              tier labels and centre stay fixed. Node labels counter-rotate
              (nx-unspin) so they remain upright while orbiting. */}
          <g className="nx-spin">
          {/* edges (you → entity) */}
          {graph.nodes.map(({ e, x, y, edge }) => (
            <line
              key={`edge-${e.key}`}
              x1={CX}
              y1={CY}
              x2={x}
              y2={y}
              stroke={typeColor(e.type)}
              strokeWidth={edge}
              strokeLinecap="round"
              opacity={selected && selected !== e.key ? 0.18 : 0.5}
            />
          ))}

          {/* entity nodes */}
          {graph.nodes.map(({ e, x, y, size }) => {
            const on = e.key === selected;
            const color = typeColor(e.type);
            return (
              <g
                key={e.key}
                className="nx-node"
                tabIndex={0}
                role="button"
                aria-label={t('you.network.nodeAria', { name: e.name, count: e.known_by.length })}
                onClick={() => setSelected(on ? null : e.key)}
                onKeyDown={(ev) => {
                  if (ev.key === 'Enter' || ev.key === ' ') {
                    ev.preventDefault();
                    setSelected(on ? null : e.key);
                  }
                }}
                opacity={selected && !on ? 0.45 : 1}
              >
                {/* soft glow signalling clickability / selection */}
                <circle
                  className="nx-glow"
                  cx={x}
                  cy={y}
                  r={size / 2 + 5}
                  fill="none"
                  stroke={color}
                  strokeWidth={1.5}
                  opacity={on ? 0.9 : 0}
                />
                <circle cx={x} cy={y} r={size / 2} fill={color} />
                <text
                  className="nx-unspin"
                  x={x}
                  y={y + size / 2 + 12}
                  textAnchor="middle"
                  fontSize={11}
                  fill="var(--text-secondary)"
                >
                  {e.name.length > 14 ? `${e.name.slice(0, 13)}…` : e.name}
                </text>
                {/* big hit area */}
                <circle className="nx-hit" cx={x} cy={y} r={Math.max(size, 22)} fill="transparent" />
              </g>
            );
          })}
          </g>

          {/* centre — you */}
          <circle cx={CX} cy={CY} r={26} fill="none" stroke="var(--color-carbon)" strokeWidth={2} />
          <circle cx={CX} cy={CY} r={22} fill="var(--color-carbon)" />
          <text x={CX} y={CY + 1} textAnchor="middle" dominantBaseline="central" fontSize={13} fill="#fff">
            {t('you.network.youCenter')}
          </text>
          <text
            x={CX}
            y={CY + 40}
            textAnchor="middle"
            fontSize={11}
            fill="var(--text-secondary)"
            fontFamily="var(--font-mono)"
          >
            {youName.length > 18 ? `${youName.slice(0, 17)}…` : youName}
          </text>
        </svg>
      </div>

      {/* selected detail */}
      {selectedItem && (
        <div className="mt-2 shrink-0 rounded-[var(--radius-md)] border border-[var(--nm-hairline)] bg-[var(--bg-primary)] p-3">
          <div className="flex items-center gap-2 mb-1">
            <span
              className="w-2.5 h-2.5 rounded-full allow-circle shrink-0"
              style={{ background: typeColor(selectedItem.type) }}
              aria-hidden
            />
            <span className="text-[13px] font-medium text-[var(--text-primary)] truncate">
              {selectedItem.name}
            </span>
            <span className="text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.1em] text-[var(--text-tertiary)]">
              {selectedItem.type} · {selectedItem.familiarity.replace('_', ' ')}
            </span>
          </div>
          {selectedItem.description && (
            <p className="text-[12px] leading-relaxed text-[var(--text-secondary)] line-clamp-3">
              {selectedItem.description.split('\n')[0]}
            </p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.1em] text-[var(--text-tertiary)]">
            <span className="inline-flex items-center gap-1">
              {t('you.network.knownBy')}
              <span className="text-[var(--text-secondary)] normal-case tracking-normal">
                {selectedItem.known_by.join(', ')}
              </span>
            </span>
            {selectedItem.interactions > 0 && <span>{t('you.network.interactions', { count: selectedItem.interactions })}</span>}
            {selectedItem.last_interaction_time && (
              <span>{t('you.network.seen', { date: fmtDay.format(new Date(selectedItem.last_interaction_time)) })}</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

interface PlacedNode {
  e: MyNetworkEntity;
  x: number;
  y: number;
  size: number;
  edge: number;
}

function Center({ children }: { children: React.ReactNode }) {
  return <div className="w-full h-full flex items-center justify-center p-8">{children}</div>;
}

export default NexusNetworkGraph;
