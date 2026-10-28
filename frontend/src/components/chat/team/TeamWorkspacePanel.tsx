/**
 * @file_name: TeamWorkspacePanel.tsx
 * @author: NarraNexus
 * @date: 2026-08-07
 * @description: The team room's workspace — artifacts and shared files.
 *
 * Until now a team's output had nowhere to live. An artifact registered during
 * a team turn appeared only in the producing agent's private chat, so seeing
 * "what the team made" meant leaving the room and opening one member's
 * one-to-one conversation; shared files had no UI at all, and the only way to
 * find one was an agent reciting an absolute path into the chat.
 *
 * One panel, two modes: the mental model is "the team's workspace", so both
 * modes share the same list/viewer layout and the preview region stays visible
 * whichever one is active — a chip in the transcript can open an artifact
 * without yanking the user out of Files.
 *
 * Every row is attributed. In a one-to-one chat "who made this" is never a
 * question; here several agents write into the same space, and an unattributed
 * list is how a shared workspace turns into an anonymous pile.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Maximize2 } from 'lucide-react';

import ArtifactRenderer from '@/components/artifacts/ArtifactRenderer';
import ArtifactZoomModal from '@/components/artifacts/ArtifactZoomModal';
import { api } from '@/lib/api';
import { activeLocale, formatMessageAge } from '@/lib/utils';
import type { Artifact, TeamFile } from '@/types/artifact';

interface TeamWorkspacePanelProps {
  /** Which panel the drawer has open — the member-bar toggles and the drawer title switcher own this. */
  tab: 'artifacts' | 'files';
  artifacts: Artifact[];
  files: TeamFile[];
  loading: boolean;
  error: string | null;
  /**
   * Selection is CONTROLLED by the parent. A chip under a message has to be
   * able to open something here, so exactly one component can own "what is
   * showing" — and it has to be the one both the transcript and this panel
   * live inside.
   */
  selectedId: string | null;
  onSelect: (artifactId: string | null) => void;
}

function formatSize(bytes: number): string {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function TeamWorkspacePanel({
  tab,
  artifacts,
  files,
  loading,
  error,
  selectedId,
  onSelect,
}: TeamWorkspacePanelProps) {
  const { t } = useTranslation();
  // Download failures are the panel's own, not the parent's fetch error.
  const [localError, setLocalError] = useState<string | null>(null);
  // Fullscreen zoom for the selected artifact (same modal as ArtifactColumn).
  const [zoomed, setZoomed] = useState(false);

  // Rendered in place rather than opened in a new tab: the panel exists so a
  // team's output can be read WHILE reading the conversation that produced it,
  // and a new tab throws that context away.
  //
  // ArtifactRenderer needs no team plumbing. It mints through the agent-scoped
  // route using the artifact's own agent_id — the producer — and that route
  // authorises on "does the JWT user own this agent". Every member of a team is
  // an agent of the team's single owner, so a teammate's artifact resolves
  // without a team-specific token path.
  const selected = artifacts.find((a) => a.artifact_id === selectedId) ?? null;

  // No tab switching when a chip selects something: the preview is its own
  // region BELOW the list, so it shows regardless of which tab is active.
  // Forcing the tab would also yank the user out of Files mid-browse.

  // A plain <a href> cannot work here: the endpoint is JWT/X-User-Id gated and
  // a browser navigation attaches neither. Reuses the same authed fetch the
  // bus-attachment hook already uses, then hands the bytes to a temporary
  // object URL — no second download route to secure.
  const download = async (file: TeamFile) => {
    try {
      const blob = await api.fetchBusAttachmentBlob(file.rel_path);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = file.original_name;
      // Attached, and the URL revoked on a later tick: Firefox aborts a large
      // download when the object URL dies in the same task as the click, and
      // a detached anchor is unreliable there too.
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (e) {
      setLocalError(e instanceof Error ? e.message : 'Download failed');
    }
  };

  return (
    // Pure drawer content: the shared BookmarkDrawer owns the shell (title switcher,
    // pin, close, width); this fills it.
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-1 min-h-0">
      {/* Master list: a picker, not the main event — it yields so the
          viewer keeps a readable share of the (user-resizable, shared-
          preference) drawer width even at the 400px default. */}
      <div className="w-40 min-w-[7rem] shrink border-r border-[var(--nm-hairline)] overflow-y-auto min-h-0">
        {(error || localError) && (
          <div className="px-3 py-2 text-[11px] text-[var(--nm-danger,#c0392b)]">
            {error || localError}
          </div>
        )}

        {tab === 'artifacts' &&
          (artifacts.length === 0 ? (
            <EmptyState
              loading={loading}
              // Names the mechanism rather than just saying "empty": the user's
              // next question is always "how does something get in here".
              hint={t('chat.team.workspace.artifactsHint')}
            />
          ) : (
            artifacts.map((a) => (
              <button
                key={a.artifact_id}
                type="button"
                onClick={() => onSelect(a.artifact_id)}
                className={`w-full text-left px-3 py-2 border-b border-[var(--nm-hairline)] transition-colors hover:bg-[var(--nm-row-hover)] ${
                  selectedId === a.artifact_id ? 'bg-[var(--nm-row-active)]' : ''
                }`}
              >
                <div className="text-xs text-[var(--nm-ink)] truncate">{a.title}</div>
                <div className="mt-0.5 text-[10px] font-mono text-[var(--text-tertiary)] truncate">
                  {a.agent_id} · {formatMessageAge(a.updated_at, activeLocale())}
                </div>
              </button>
            ))
          ))}

        {tab === 'files' &&
          (files.length === 0 ? (
            <EmptyState
              loading={loading}
              hint={t('chat.team.workspace.filesHint')}
            />
          ) : (
            files.map((f) => (
              <button
                key={f.file_id}
                type="button"
                onClick={() => void download(f)}
                title={t('chat.team.workspace.download', { name: f.original_name })}
                className="w-full text-left px-3 py-2 border-b border-[var(--nm-hairline)] transition-colors hover:bg-[var(--nm-row-hover)]"
              >
                <div className="text-xs text-[var(--nm-ink)] truncate">{f.original_name}</div>
                <div className="mt-0.5 text-[10px] font-mono text-[var(--text-tertiary)] truncate">
                  {f.shared_by_agent_id} · {formatMessageAge(f.created_at, activeLocale())}
                  {f.size_bytes ? ` · ${formatSize(f.size_bytes)}` : ''}
                </div>
              </button>
            ))
          ))}
      </div>

      {/* Viewer — the drawer's main region, full height like the single-chat
          artifact column. The list on the left picks; this side shows. */}
      <div className="flex min-w-0 flex-1 flex-col min-h-0">
        {selected ? (
          <>
            <div className="shrink-0 flex items-center justify-between gap-1 px-3 py-1.5 border-b border-[var(--nm-hairline)]">
              <span className="text-[10px] font-mono text-[var(--text-tertiary)] truncate">
                {selected.title}
              </span>
              <button
                type="button"
                onClick={() => setZoomed(true)}
                title={t('chat.team.workspace.zoom')}
                aria-label={t('chat.team.workspace.zoom')}
                className="shrink-0 p-0.5 text-[var(--text-tertiary)] hover:text-[var(--nm-ink)]"
              >
                <Maximize2 className="h-3.5 w-3.5" />
              </button>
            </div>
            <div className="flex-1 min-h-0 overflow-hidden">
              <ArtifactRenderer artifact={selected} />
            </div>
          </>
        ) : (
          <div className="flex flex-1 items-center justify-center px-6 text-center text-[11px] text-[var(--text-tertiary)]">
            {tab === 'artifacts'
              ? t('chat.team.workspace.emptyArtifacts')
              : t('chat.team.workspace.emptyFiles')}
          </div>
        )}
      </div>
      </div>

      {zoomed && (
        <ArtifactZoomModal artifact={selected} onClose={() => setZoomed(false)} />
      )}
    </div>
  );
}

function EmptyState({ loading, hint }: { loading: boolean; hint: string }) {
  const { t } = useTranslation();
  return (
    <div className="px-3 py-6 text-[11px] text-[var(--text-tertiary)]">
      {loading ? t('chat.team.workspace.loading') : hint}
    </div>
  );
}
