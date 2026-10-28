/**
 * @file_name: TeamMessageFooter.tsx
 * @author: NarraNexus
 * @date: 2026-08-12
 * @description: What hangs under one team message: what it made and when it
 * was said. Its process opens from the top of the bubble (single-chat parity).
 *
 * Lifted out of TeamChatPanel unchanged — the joins and their reasoning are the
 * ones that were already there. It moved so the bubble can own how a message
 * LOOKS without also owning what the room knows about it.
 */

import { formatTime } from '@/lib/utils';
import type { Artifact } from '@/types/artifact';
import type { TeamChatMessage } from '@/types/teams';

export interface TeamMessageFooterProps {
  message: TeamChatMessage;
  /** Artifact ids this turn produced. */
  turnArtifacts: string[];
  /** The team's artifacts, for resolving a title. */
  artifacts: Artifact[];
  onOpenArtifact: (artifactId: string) => void;
}

export function TeamMessageFooter({
  message: m,
  turnArtifacts,
  artifacts,
  onOpenArtifact,
}: TeamMessageFooterProps) {
  const ts = Date.parse(m.created_at);

  return (
    <>
      {/* The process disclosure sits at the TOP of the bubble (single-chat
          parity) — see TeamChatPanel's renderHeader. This footer keeps what
          hangs under the content: artifacts and the timestamp. */}
      {/* What THIS turn produced. Joined on event_id, which both the transcript
          and the artifact history carry — not on timestamps, which would
          mis-attribute the ordinary cases (two artifacts in one turn, two agents
          replying at once). */}
      {turnArtifacts.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {turnArtifacts.map((aid) => {
            const art = artifacts.find((x) => x.artifact_id === aid);
            return (
              <button
                key={aid}
                type="button"
                onClick={() => onOpenArtifact(aid)}
                title="Open in the team workspace"
                className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-mono border border-[var(--nm-hairline)] text-[var(--text-tertiary)] hover:text-[var(--nm-ink)] max-w-full"
              >
                <span className="truncate">{art?.title ?? aid}</span>
                <span className="opacity-50">↗</span>
              </button>
            );
          })}
        </div>
      )}

      <div
        className="mt-1 font-mono tracking-wide"
        style={{
          color: 'var(--nm-subtle)',
          fontSize: '9.5px',
          letterSpacing: '0.05em',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {Number.isFinite(ts) ? formatTime(ts) : ''}
      </div>
    </>
  );
}

export default TeamMessageFooter;
