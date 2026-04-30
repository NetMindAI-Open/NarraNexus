/**
 * @file_name: SessionSection.tsx
 * @description: v2.1 — collapsible "Active sessions" section. Shows avatar
 * strip when collapsed, full list when expanded. Individual sessions have
 * their own item-level expand with lazy-loaded detail.
 */
import { useState } from 'react';
import type { SessionInfoResp } from '@/types';
import { api } from '@/lib/api';
import { useExpanded } from './expandState';

interface Props {
  agentId: string;
  sessions: SessionInfoResp[];
}

/** Stable color assignment from a string (user_id or session_id). */
function colorForSeed(seed: string): string {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) {
    hash = (hash * 31 + seed.charCodeAt(i)) | 0;
  }
  const palette = [
    'bg-[var(--color-green-500)]', 'bg-sky-500', 'bg-[var(--color-yellow-500)]', 'bg-rose-500',
    'bg-violet-500', 'bg-fuchsia-500', 'bg-teal-500', 'bg-indigo-500',
  ];
  return palette[Math.abs(hash) % palette.length];
}

function initials(display: string): string {
  const parts = display.trim().split(/\s+/);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function AvatarDot({ seed, display }: { seed: string; display: string }) {
  return (
    <span
      title={display}
      data-testid={`session-avatar-${seed}`}
      className={`inline-flex h-5 w-5 items-center justify-center rounded-full text-[9px] font-semibold text-white ${colorForSeed(seed)}`}
    >
      {initials(display)}
    </span>
  );
}

export function SessionSection({ agentId, sessions }: Props) {
  const { expanded, toggle } = useExpanded(`${agentId}:section:sessions`, false);
  if (!sessions || sessions.length === 0) return null;

  const channels = Array.from(new Set(sessions.map((s) => s.channel))).slice(0, 3);
  const maxAvatars = 5;
  const shown = sessions.slice(0, maxAvatars);
  const extra = sessions.length - shown.length;

  return (
    <div className="text-xs">
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); toggle(); }}
        className="flex w-full items-center gap-2 text-left hover:opacity-90"
        aria-expanded={expanded}
      >
        <span className={`transition-transform ${expanded ? 'rotate-90' : ''}`}>▸</span>
        <span>💬 {sessions.length === 1 ? 'Session with' : `${sessions.length} sessions`}</span>
        <div className="flex -space-x-1">
          {shown.map((s) => (
            <AvatarDot key={s.session_id} seed={s.user_display} display={s.user_display} />
          ))}
          {extra > 0 && (
            <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-[var(--bg-tertiary)] text-[9px]">
              +{extra}
            </span>
          )}
        </div>
        <span className="text-[var(--text-secondary)] truncate">· on {channels.join(' · ')}</span>
      </button>
      {expanded && (
        <ul className="mt-1 ml-3 space-y-1 border-l-2 border-[var(--rule)] pl-2">
          {sessions.map((s) => (
            <SessionItem key={s.session_id} agentId={agentId} session={s} />
          ))}
        </ul>
      )}
    </div>
  );
}

function SessionItem({ agentId, session }: { agentId: string; session: SessionInfoResp }) {
  const { expanded, toggle } = useExpanded(
    `${agentId}:item:session:${session.session_id}`,
    false,
  );
  const [detail, setDetail] = useState<unknown | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const onClick = async (e: React.MouseEvent) => {
    e.stopPropagation();
    toggle();
    if (!expanded && detail === null && !loading) {
      setLoading(true);
      try {
        const res = await api.getSessionDetail(session.session_id, agentId);
        setDetail(res.session);
      } catch (e: unknown) {
        setErr(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    }
  };

  return (
    <li className="text-[11px]">
      <button
        type="button"
        onClick={onClick}
        className="flex w-full items-center gap-2 py-0.5 text-left hover:bg-[var(--bg-tertiary)] rounded"
        aria-expanded={expanded}
      >
        <AvatarDot seed={session.user_display} display={session.user_display} />
        <span className="font-medium">{session.user_display}</span>
        <span className="text-[var(--text-secondary)]">· {session.channel}</span>
        {session.user_last_message_preview && (
          <span className="truncate text-[var(--text-secondary)]">
            · "{session.user_last_message_preview}"
          </span>
        )}
        <span className={`ml-auto transition-transform ${expanded ? 'rotate-90' : ''}`}>▸</span>
      </button>
      {expanded && (
        <div className="ml-7 mt-1 rounded border border-[var(--border-subtle)] bg-[var(--bg-sunken)] p-2 space-y-1">
          {loading && <div className="text-[var(--text-secondary)]">Loading…</div>}
          {err && <div className="text-[var(--color-red-500)]">Failed: {err}</div>}
          {detail !== null && (
            <>
              <div className="text-[var(--text-secondary)]">
                session_id: <span className="font-mono">{session.session_id.slice(0, 12)}…</span>
              </div>
              <div className="text-[var(--text-secondary)]">
                started: <span className="font-mono">{session.started_at}</span>
              </div>
              {renderLatestMessage(detail)}
            </>
          )}
        </div>
      )}
    </li>
  );
}

function renderLatestMessage(detail: unknown): React.ReactNode {
  try {
    const d = detail as { latest_message?: { content?: string; at?: string } };
    if (d?.latest_message?.content) {
      return (
        <div className="mt-1">
          <div className="text-[var(--text-secondary)]">Latest message:</div>
          <div className="mt-0.5 rounded bg-[var(--bg-secondary)] p-1.5">
            {d.latest_message.content}
          </div>
        </div>
      );
    }
  } catch {
    // ignore
  }
  return null;
}
