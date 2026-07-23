/**
 * @file_name: TeamChatPanel.tsx
 * @author:
 * @date: 2026-06-23
 * @description: Team group-chat surface. Renders one team's shared room:
 * a member bar on top (the user + member agents), the message timeline, and
 * a composer with @-mention. The user posts into the room and @-mentioned
 * agents reply; mentioning @all addresses everyone.
 *
 * Wiring: messages flow over the message bus. Send → POST
 * /api/teams/{id}/chat/messages (sender = usr_<user_id>, mentions = agent_ids
 * and/or "@all"); the standalone MessageBusTrigger runs the @mentioned agents
 * and posts their replies back into the room. The panel polls
 * GET /api/teams/{id}/chat/messages for the live transcript.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { CornerDownLeft, FileText, Image as ImageIcon, Loader2, Mic, Plus, Settings2, Users2, X } from 'lucide-react';
import { RingAvatar } from '@/components/nm';
import { Button, Textarea, Markdown } from '@/components/ui';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/Dialog';
import { BusAttachmentList } from './BusAttachmentList';
import { AudioRecorder } from './AudioRecorder';
import { VoiceTranscript } from './VoiceTranscript';
import { useTeamsStore, useConfigStore } from '@/stores';
import { api } from '@/lib/api';
import { cn, formatTime } from '@/lib/utils';
import type { AgentInfo } from '@/types';
import type { TeamChatMessage, TeamMemberActivity } from '@/types/teams';
import type { BusAttachment } from '@/types';

interface TeamChatPanelProps {
  teamId: string;
}

/** A mention-dropdown option: the @all broadcast, or a specific teammate. */
type MentionOption = { kind: 'all' } | { kind: 'agent'; agent: AgentInfo };

const POLL_MS = 3000;

export function TeamChatPanel({ teamId }: TeamChatPanelProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const teams = useTeamsStore((s) => s.teams);
  const agents = useConfigStore((s) => s.agents);
  const displayName = useConfigStore((s) => s.displayName);
  const userId = useConfigStore((s) => s.userId);
  const userLabel = displayName || userId;

  const team = useMemo(
    () => teams.find((t) => t.team.team_id === teamId) ?? null,
    [teams, teamId],
  );

  // Resolve the team's member agents (preserve team membership order).
  const members = useMemo(() => {
    if (!team) return [];
    return team.member_agent_ids
      .map((aid) => agents.find((a) => a.agent_id === aid))
      .filter((a): a is NonNullable<typeof a> => !!a);
  }, [team, agents]);

  const [text, setText] = useState('');
  const [messages, setMessages] = useState<TeamChatMessage[]>([]);
  const [thinking, setThinking] = useState<string[]>([]);
  const [activity, setActivity] = useState<TeamMemberActivity[]>([]);
  // 1s ticker so a running agent's elapsed time updates between 3s polls.
  const [, setNowTick] = useState(0);
  const [sending, setSending] = useState(false);
  const [pending, setPending] = useState<BusAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  // Voice input (mirrors the single-agent ChatPanel): probe transcription
  // availability once per user; a mic click when unavailable opens a dialog.
  const [transcriptionAvailable, setTranscriptionAvailable] = useState<boolean | undefined>(undefined);
  const [transcriptionReason, setTranscriptionReason] = useState<string>('');
  const [voiceUnavailableDialogOpen, setVoiceUnavailableDialogOpen] = useState(false);
  const [transcriptionNotice, setTranscriptionNotice] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    api
      .getTranscriptionAvailability()
      .then((r) => {
        if (cancelled) return;
        setTranscriptionAvailable(r.available);
        setTranscriptionReason(r.reason);
      })
      .catch(() => {
        // Probe failure → leave undefined so the click is allowed; a real
        // failure surfaces via the post-upload notice.
      });
    return () => {
      cancelled = true;
    };
  }, [userId]);
  const endRef = useRef<HTMLDivElement | null>(null);

  // --- Live transcript: poll the room while the panel is open. -------------
  const refresh = useCallback(async () => {
    try {
      const r = await api.getTeamChat(teamId);
      if (r.success) {
        setMessages(r.messages);
        setThinking(r.thinking ?? []);
        setActivity(r.activity ?? []);
      }
    } catch {
      // transient — the next tick retries
    }
  }, [teamId]);

  useEffect(() => {
    let alive = true;
    setMessages([]);
    setThinking([]);
    setActivity([]);
    refresh();
    const id = window.setInterval(() => { if (alive) refresh(); }, POLL_MS);
    return () => { alive = false; window.clearInterval(id); };
  }, [refresh]);

  // Tick every 1s while any agent is running so its elapsed time advances.
  const anyRunning = activity.some((a) => a.status === 'running');
  useEffect(() => {
    if (!anyRunning) return;
    const id = window.setInterval(() => setNowTick((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [anyRunning]);

  // Human phase label for a running agent (starting/thinking/replying/tool:X).
  const phaseLabel = (phase?: string | null): string => {
    if (!phase) return t('chat.team.activity.running');
    if (phase.startsWith('tool:')) return t('chat.team.activity.tool', { name: phase.slice(5) });
    if (phase === 'thinking') return t('chat.team.activity.thinking');
    if (phase === 'replying') return t('chat.team.activity.replying');
    return t('chat.team.activity.running');
  };
  const elapsedLabel = (startedAt?: string | null): string => {
    if (!startedAt) return '';
    const secs = Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000));
    return secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m${secs % 60}s`;
  };

  // Keep the latest message in view as the transcript grows.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' });
  }, [messages.length]);

  // --- @-mention autocomplete. `start` is the active '@' index in `text`. ---
  const [mention, setMention] = useState<{ open: boolean; start: number; query: string }>(
    { open: false, start: 0, query: '' },
  );
  const [mentionIndex, setMentionIndex] = useState(0);

  const mentionOptions: MentionOption[] = useMemo(() => {
    if (!mention.open) return [];
    const q = mention.query.toLowerCase();
    const opts: MentionOption[] = [];
    // @all leads the list when its label is still a prefix match.
    if (members.length > 0 && ('all'.startsWith(q) || 'everyone'.startsWith(q))) {
      opts.push({ kind: 'all' });
    }
    for (const m of members) {
      if ((m.name || m.agent_id).toLowerCase().includes(q)) opts.push({ kind: 'agent', agent: m });
    }
    return opts;
  }, [mention.open, mention.query, members]);

  const closeMention = () => setMention({ open: false, start: 0, query: '' });

  // Detect an active "@query" run ending at the caret: the '@' must sit at the
  // start or after whitespace, and the run itself must contain no whitespace.
  const syncMention = (value: string, caret: number) => {
    const upto = value.slice(0, caret);
    const at = upto.lastIndexOf('@');
    if (at === -1) return closeMention();
    const before = at === 0 ? '' : upto[at - 1];
    const query = upto.slice(at + 1);
    if ((before && !/\s/.test(before)) || /\s/.test(query)) return closeMention();
    setMention({ open: true, start: at, query });
    setMentionIndex(0);
  };

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    setText(value);
    syncMention(value, e.target.selectionStart ?? value.length);
  };

  const applyMentionOption = (opt: MentionOption | undefined) => {
    if (!opt) return;
    const label = opt.kind === 'all' ? 'all' : (opt.agent.name || opt.agent.agent_id);
    const before = text.slice(0, mention.start);
    const after = text.slice(mention.start + 1 + mention.query.length);
    const caret = `${before}@${label} `.length;
    setText(`${before}@${label} ${after}`);
    closeMention();
    requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(caret, caret);
    });
  };

  // Top-bar avatar click → append a mention (no active caret context).
  const insertMention = (name: string) => {
    setText((t) => `${t}${t && !t.endsWith(' ') ? ' ' : ''}@${name} `);
    closeMention();
    inputRef.current?.focus();
  };

  /** Resolve the @tokens in the composed text to agent_ids and/or "@all". */
  const resolveMentions = (value: string): string[] => {
    const tokens = new Set(
      (value.match(/@([\w一-鿿]+)/g) || []).map((s) => s.slice(1).toLowerCase()),
    );
    if (tokens.size === 0) return [];
    if (tokens.has('all') || tokens.has('everyone')) return ['@all'];
    const ids: string[] = [];
    for (const m of members) {
      const nm = (m.name || m.agent_id).toLowerCase();
      const first = nm.split(/\s+/)[0];
      if (tokens.has(nm) || tokens.has(first) || [...tokens].some((t) => t.length >= 2 && nm.startsWith(t))) {
        ids.push(m.agent_id);
      }
    }
    return ids;
  };

  const handlePickFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        const res = await api.uploadTeamChatAttachment(teamId, file);
        if (res.success && res.attachment) {
          setPending((prev) => [...prev, res.attachment!]);
        }
      }
    } catch {
      // Silent — a failed upload just doesn't add a chip; the user can retry.
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleRecorded = async (file: File) => {
    setUploading(true);
    try {
      const res = await api.uploadTeamChatAttachment(teamId, file, { source: 'recording' });
      if (res.success && res.attachment) {
        setPending((prev) => [...prev, res.attachment!]);
        setTranscriptionNotice(
          res.transcription_available === false ? t('chat.team.transcriptionUnavailable') : null,
        );
      }
    } catch {
      // Silent — the AudioRecorder's own onError surfaces capture failures.
    } finally {
      setUploading(false);
    }
  };

  const handleSend = async () => {
    const body = text.trim();
    if ((!body && pending.length === 0) || sending || uploading) return;
    const mentions = resolveMentions(body);
    const attachments = pending;
    setText('');
    setPending([]);
    closeMention();
    setSending(true);
    try {
      await api.sendTeamChat(teamId, body, mentions, attachments);
      await refresh();
    } catch {
      // Restore the draft + attachments so nothing is lost on a failed send.
      setText(body);
      setPending(attachments);
    } finally {
      setSending(false);
    }
  };

  if (!team) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-[var(--text-tertiary)]">
        {t('chat.team.notFound')}
      </div>
    );
  }

  const accent = team.team.color || 'var(--color-silicon)';

  return (
    <div className="flex h-full flex-col min-h-0">
      {/* Member bar — team identity + the roster of agents in this room. */}
      <div className="shrink-0 flex items-center gap-3 px-5 py-2.5 border-b border-[var(--nm-hairline)]">
        <span
          className="w-2.5 h-2.5 rounded-full shrink-0"
          style={{ backgroundColor: accent }}
          aria-hidden
        />
        <div className="min-w-0">
          <div className="text-sm font-medium text-[var(--nm-ink)] truncate">
            {team.team.name}
          </div>
          <div className="text-[10px] font-mono uppercase tracking-wider text-[var(--text-tertiary)]">
            {t('chat.team.memberCount', { count: members.length })}
          </div>
        </div>

        {/* Roster — the user (carbon/human) sits first, then the team's agents
            (silicon). The user is a participant in this room, so their avatar
            belongs in the bar alongside the agents. */}
        <div className="flex items-center gap-1.5 ml-2 overflow-x-auto">
          <span title={t('chat.team.youTitle', { name: userLabel })} className="shrink-0">
            <RingAvatar species="carbon" label={(userLabel || '?').slice(0, 2)} size="sm" />
          </span>
          {members.length > 0 && (
            <span className="w-px h-5 bg-[var(--nm-hairline)] mx-0.5 shrink-0" aria-hidden />
          )}
          {members.map((m) => (
            <button
              key={m.agent_id}
              type="button"
              onClick={() => insertMention(m.name || m.agent_id)}
              title={`@${m.name || m.agent_id}`}
              className="shrink-0 rounded-full transition-transform hover:-translate-y-0.5"
            >
              <RingAvatar species="silicon" label={(m.name || m.agent_id).slice(0, 2)} size="sm" />
            </button>
          ))}
          {members.length === 0 && (
            <span className="text-xs text-[var(--text-tertiary)]">{t('chat.team.noAgents')}</span>
          )}
        </div>

        {/* Team settings (detail page). */}
        <button
          type="button"
          onClick={() => navigate(`/app/teams/${teamId}`)}
          title={t('chat.team.teamSettings')}
          aria-label={t('chat.team.teamSettings')}
          className="ml-auto shrink-0 flex h-7 w-7 items-center justify-center rounded-[var(--radius-xs)] text-[var(--text-secondary)] transition-colors hover:bg-[var(--nm-paper-warm)] hover:text-[var(--color-carbon)]"
        >
          <Settings2 className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Team activity strip — who's running / queued right now (at a glance). */}
      {activity.some((a) => a.status !== 'idle') && (
        <div className="shrink-0 flex flex-wrap items-center gap-1.5 px-5 py-2 border-b border-[var(--rule)] bg-[var(--nm-paper-warm)]/40">
          {activity
            .filter((a) => a.status !== 'idle')
            .map((a) => {
              const name = members.find((m) => m.agent_id === a.agent_id)?.name || a.agent_id;
              const running = a.status === 'running';
              return (
                <span
                  key={`act-${a.agent_id}`}
                  className="inline-flex items-center gap-1.5 rounded-full border border-[var(--rule)] bg-[var(--nm-paper)] px-2 py-0.5 text-[11px]"
                  title={running ? `${phaseLabel(a.phase)} · ${elapsedLabel(a.started_at)}` : t('chat.team.activity.queued')}
                >
                  <span
                    className={cn('h-1.5 w-1.5 rounded-full', running && 'animate-pulse')}
                    style={{ background: running ? 'var(--color-silicon)' : 'var(--color-amber-500, #d97706)' }}
                  />
                  <span className="font-medium text-[var(--nm-ink)]">{name}</span>
                  <span className="text-[var(--text-tertiary)]">
                    {running
                      ? `${phaseLabel(a.phase)} · ${elapsedLabel(a.started_at)}`
                      : t('chat.team.activity.queued')}
                  </span>
                </span>
              );
            })}
        </div>
      )}

      {/* Timeline */}
      <div className="flex-1 min-h-0 overflow-y-auto px-5 py-4">
        {messages.length === 0 && thinking.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-center gap-2 text-[var(--text-tertiary)]">
            <Users2 className="w-6 h-6 opacity-40" />
            <div className="text-sm">{t('chat.team.empty')}</div>
            <div className="text-xs max-w-[260px]">
              {t('chat.team.emptyHint')}
            </div>
          </div>
        ) : (
          <div className="space-y-5">
            {messages.map((m) => {
              const mine = m.is_user;
              const avatarLabel = (mine ? userLabel : m.author_name) || '?';
              const ts = Date.parse(m.created_at);
              return (
                <div key={m.message_id} className={cn('flex gap-3', mine && 'flex-row-reverse')}>
                  {/* Carbon ring for the human, silicon for an agent — matching
                      the single-agent MessageBubble. Hidden on mobile. */}
                  <RingAvatar
                    species={mine ? 'carbon' : 'silicon'}
                    label={avatarLabel.slice(0, 2)}
                    size="sm"
                    className="shrink-0 hidden md:inline-flex"
                  />
                  <div className={cn('flex-1 min-w-0', mine && 'text-right')}>
                    {/* Author name above an agent bubble — a group chat has
                        multiple speakers, so name them (single-agent doesn't). */}
                    {!mine && (
                      <div className="mb-0.5 px-0.5 text-[10px] font-mono text-[var(--text-tertiary)]">
                        {m.author_name}
                      </div>
                    )}
                    <div
                      className={cn(
                        'relative inline-block max-w-[85%] text-left px-3.5 py-2.5 rounded-[var(--radius-lg)] transition-colors duration-150',
                        !mine && 'nm-bubble-ai',
                      )}
                      style={
                        mine
                          ? {
                              background: 'var(--color-carbon-soft)',
                              color: 'var(--nm-ink)',
                              border: '1px solid var(--color-carbon-hair)',
                              borderRight: '3px solid var(--color-carbon)',
                            }
                          : {
                              background: 'var(--color-silicon-soft)',
                              color: 'var(--nm-ink)',
                              border: '1px solid var(--color-silicon-hair)',
                              borderLeft: '3px solid var(--color-silicon)',
                            }
                      }
                    >
                      <div className="text-sm break-words leading-relaxed">
                        {mine ? (
                          <span className="whitespace-pre-wrap text-[0.875rem] md:text-[0.95rem]">
                            {m.content}
                          </span>
                        ) : (
                          // Agent replies are markdown (bold, lists, code) —
                          // render them like the single-agent bubble; this also
                          // collapses the stray leading whitespace agents emit.
                          <Markdown content={m.content.trim()} />
                        )}
                      </div>
                      <BusAttachmentList attachments={m.attachments} />
                    </div>
                    {/* Meta row outside the bubble, aligned to its side. */}
                    <div
                      className={cn(
                        'mt-1 flex items-center gap-1.5 px-0.5',
                        mine ? 'justify-end' : 'justify-start',
                      )}
                    >
                      <span
                        className="font-mono tracking-wide"
                        style={{
                          color: 'var(--nm-subtle)',
                          fontSize: '9.5px',
                          letterSpacing: '0.05em',
                          fontVariantNumeric: 'tabular-nums',
                        }}
                      >
                        {Number.isFinite(ts) ? formatTime(ts) : ''}
                      </span>
                    </div>
                  </div>
                </div>
              );
            })}

            {/* Activity bubble per active member: running shows its live phase
                + elapsed; queued shows the "…" waiting dots. */}
            {activity
              .filter((a) => a.status !== 'idle')
              .map((a) => {
                const m = members.find((mm) => mm.agent_id === a.agent_id);
                const name = m?.name || a.agent_id;
                const running = a.status === 'running';
                return (
                  <div key={`act-bubble-${a.agent_id}`} className="flex gap-3">
                    <RingAvatar
                      species="silicon"
                      label={name.slice(0, 2)}
                      size="sm"
                      className="shrink-0 hidden md:inline-flex"
                    />
                    <div className="flex-1 min-w-0">
                      <div className="mb-0.5 px-0.5 text-[10px] font-mono text-[var(--text-tertiary)]">
                        {name}
                      </div>
                      <div
                        className="relative inline-flex items-center gap-2 px-3.5 py-2.5 rounded-[var(--radius-lg)] nm-bubble-ai"
                        style={{
                          background: 'var(--color-silicon-soft)',
                          border: '1px solid var(--color-silicon-hair)',
                          borderLeft: '3px solid var(--color-silicon)',
                        }}
                        aria-label={running ? phaseLabel(a.phase) : t('chat.team.activity.queued')}
                      >
                        {running ? (
                          <>
                            <Loader2 className="w-3.5 h-3.5 animate-spin text-[var(--color-silicon)]" />
                            <span className="text-xs text-[var(--nm-ink)]">{phaseLabel(a.phase)}</span>
                            <span className="text-[10px] font-mono text-[var(--text-tertiary)]">
                              {elapsedLabel(a.started_at)}
                            </span>
                          </>
                        ) : (
                          <>
                            {[0, 1, 2].map((i) => (
                              <span
                                key={i}
                                className="w-1.5 h-1.5 rounded-full animate-bounce"
                                style={{ background: 'var(--color-silicon)', animationDelay: `${i * 0.15}s` }}
                              />
                            ))}
                            <span className="text-[10px] text-[var(--text-tertiary)]">
                              {t('chat.team.activity.queued')}
                            </span>
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            <div ref={endRef} />
          </div>
        )}
      </div>

      {/* Composer — matches the single-agent ChatPanel: a top rule, the
          Textarea owns the box, and the send (↵) button docks bottom-right
          inside it (carbon-soft when there's content, neutral when empty). */}
      <div className="shrink-0 px-5 py-4 border-t border-[var(--rule)]">
        {/* Transcription-unavailable notice (post-record). */}
        {transcriptionNotice && (
          <div className="mb-2 flex items-start gap-2 rounded-md border border-[var(--rule)] bg-[var(--bg-tertiary)]/40 px-2.5 py-1.5 text-xs text-[var(--text-secondary)]">
            <Mic className="w-3.5 h-3.5 shrink-0 mt-0.5 text-[var(--text-tertiary)]" />
            <span className="flex-1">{transcriptionNotice}</span>
            <button
              type="button"
              onClick={() => setTranscriptionNotice(null)}
              className="p-0.5 rounded hover:bg-[var(--bg-secondary)]"
            >
              <X className="w-3 h-3 text-[var(--text-tertiary)]" />
            </button>
          </div>
        )}
        {/* Pending attachments preview row — matches the single-agent ChatPanel:
            voice memos render as a transcript chip; other files as icon + name. */}
        {(pending.length > 0 || uploading) && (
          <div className="mb-2.5 flex flex-wrap gap-2">
            {pending.map((att) => (
              <div
                key={att.file_id}
                className="relative flex items-center gap-2 rounded-md border border-[var(--rule)] bg-[var(--bg-tertiary)]/60 pr-7 pl-1.5 py-1 max-w-[300px]"
              >
                {att.source === 'recording' ? (
                  <VoiceTranscript compact transcript={att.transcript} />
                ) : (
                  <>
                    <div className="w-9 h-9 rounded bg-[var(--bg-secondary)] flex items-center justify-center shrink-0">
                      {att.category === 'image' ? (
                        <ImageIcon className="w-4 h-4 text-[var(--text-tertiary)]" />
                      ) : (
                        <FileText className="w-4 h-4 text-[var(--text-tertiary)]" />
                      )}
                    </div>
                    <div className="min-w-0 flex-1 leading-tight">
                      <div className="text-xs truncate">{att.original_name}</div>
                      <div className="text-[10px] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)] uppercase tracking-[0.1em]">
                        {att.category} · {Math.max(1, Math.round(att.size_bytes / 1024))} KB
                      </div>
                    </div>
                  </>
                )}
                <button
                  type="button"
                  onClick={() => setPending((prev) => prev.filter((a) => a.file_id !== att.file_id))}
                  className="absolute right-1 top-1 p-0.5 rounded hover:bg-[var(--bg-secondary)]"
                  title={t('chat.team.removeAttachment')}
                >
                  <X className="w-3 h-3 text-[var(--text-tertiary)]" />
                </button>
              </div>
            ))}
            {uploading && (
              <div className="flex items-center gap-1.5 px-2 py-1 rounded-md border border-dashed border-[var(--rule)] text-[10px] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)] uppercase tracking-[0.1em]">
                <Loader2 className="w-3 h-3 animate-spin" />
                {t('chat.team.uploading')}
              </div>
            )}
          </div>
        )}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => handlePickFiles(e.target.files)}
        />
        <div className="relative">
          {/* @-mention autocomplete — opens above the composer (it's pinned to
              the bottom of the panel). @all leads the list. */}
          {mention.open && mentionOptions.length > 0 && (
            <div className="absolute bottom-full left-0 mb-2 z-30 w-64 max-h-60 overflow-y-auto rounded-[var(--radius-md)] border border-[var(--nm-hairline)] bg-[var(--nm-paper)] py-1 shadow-md">
              {mentionOptions.map((opt, i) => (
                <button
                  key={opt.kind === 'all' ? '__all__' : opt.agent.agent_id}
                  type="button"
                  onMouseDown={(e) => { e.preventDefault(); applyMentionOption(opt); }}
                  onMouseEnter={() => setMentionIndex(i)}
                  className={cn(
                    'w-full flex items-center gap-2.5 px-3 py-1.5 text-left transition-colors',
                    i === mentionIndex ? 'bg-[var(--color-carbon-soft)]' : 'hover:bg-[var(--nm-paper-warm)]',
                  )}
                >
                  {opt.kind === 'all' ? (
                    <>
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-[var(--color-carbon)] text-[var(--color-carbon)]">
                        <Users2 className="w-4 h-4" />
                      </span>
                      <span className="min-w-0">
                        <span className="block text-sm text-[var(--nm-ink)]">{t('chat.team.all')}</span>
                        <span className="block text-[10px] text-[var(--text-tertiary)]">{t('chat.team.notifyEveryone')}</span>
                      </span>
                    </>
                  ) : (
                    <>
                      <RingAvatar species="silicon" label={(opt.agent.name || opt.agent.agent_id).slice(0, 2)} size="sm" />
                      <span className="min-w-0 truncate text-sm text-[var(--nm-ink)]">
                        {opt.agent.name || opt.agent.agent_id}
                      </span>
                    </>
                  )}
                </button>
              ))}
            </div>
          )}
          <Textarea
            ref={inputRef}
            value={text}
            onChange={handleChange}
            onKeyDown={(e) => {
              if (mention.open && mentionOptions.length > 0) {
                if (e.key === 'ArrowDown') {
                  e.preventDefault();
                  setMentionIndex((idx) => (idx + 1) % mentionOptions.length);
                  return;
                }
                if (e.key === 'ArrowUp') {
                  e.preventDefault();
                  setMentionIndex((idx) => (idx - 1 + mentionOptions.length) % mentionOptions.length);
                  return;
                }
                if (e.key === 'Enter' || e.key === 'Tab') {
                  e.preventDefault();
                  applyMentionOption(mentionOptions[mentionIndex]);
                  return;
                }
                if (e.key === 'Escape') {
                  e.preventDefault();
                  closeMention();
                  return;
                }
              }
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
            rows={1}
            placeholder={t('chat.team.placeholder')}
            className="nx-composer-input block min-h-[52px] max-h-[160px] py-[14px] pr-12 leading-[24px] resize-none hover:border-[color:var(--nm-hairline)] focus:border-[color:var(--nm-hairline)]"
          />
          <Button
            variant="ghost"
            size="icon"
            onClick={handleSend}
            disabled={(!text.trim() && pending.length === 0) || sending || uploading}
            title={t('chat.team.send')}
            className={cn(
              'absolute right-2 top-1/2 -translate-y-1/2 h-9 w-9 rounded-[var(--radius-lg)] border transition-colors',
              text.trim() || pending.length > 0
                ? 'border-[var(--color-carbon)] bg-[var(--color-carbon-soft)] text-[var(--color-carbon)] hover:bg-[var(--color-carbon-soft)] hover:text-[var(--color-carbon)]'
                : 'border-[var(--nm-hairline)] bg-[var(--nm-paper-warm)] text-[var(--text-tertiary)]',
            )}
          >
            <CornerDownLeft className="w-4 h-4" />
          </Button>
        </div>
        {/* Tools row — attach (+) and voice (mic) on the left, matching the
            single-agent ChatPanel. */}
        <div className="mt-1 flex items-center gap-0.5">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading || sending}
            className="h-8 w-8 text-[var(--text-secondary)] hover:bg-transparent hover:text-[var(--color-carbon)]"
            title={t('chat.team.attach')}
          >
            <Plus className="w-4 h-4" />
          </Button>
          <AudioRecorder
            disabled={uploading || sending}
            onRecorded={handleRecorded}
            onError={(msg) => setTranscriptionNotice(msg)}
            available={transcriptionAvailable}
            onUnavailable={() => setVoiceUnavailableDialogOpen(true)}
            onPreflight={async () => {
              if (!userId) return false;
              try {
                const r = await api.getTranscriptionAvailability();
                setTranscriptionAvailable(r.available);
                setTranscriptionReason(r.reason);
                if (!r.available) {
                  setVoiceUnavailableDialogOpen(true);
                  return false;
                }
                return true;
              } catch {
                return true;
              }
            }}
          />
        </div>
      </div>

      {/* Voice-input unavailable dialog — mirrors the single-agent ChatPanel. */}
      <Dialog
        isOpen={voiceUnavailableDialogOpen}
        onClose={() => setVoiceUnavailableDialogOpen(false)}
        title={t('chat.team.voiceUnavailableTitle')}
        size="md"
      >
        <DialogContent>
          <div className="flex items-start gap-3">
            <div className="w-9 h-9 rounded-full bg-[var(--bg-tertiary)] flex items-center justify-center shrink-0">
              <Mic className="w-4 h-4 text-[var(--text-secondary)]" />
            </div>
            <div className="flex-1 text-sm leading-relaxed text-[var(--text-secondary)]">
              <p>{t('chat.team.voiceUnavailableBody')}</p>
              {transcriptionReason === 'unknown' && (
                <p className="mt-2 text-xs text-[var(--text-tertiary)] italic">
                  {t('chat.team.voiceUnavailableProbeFailed')}
                </p>
              )}
            </div>
          </div>
        </DialogContent>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setVoiceUnavailableDialogOpen(false)}>
            {t('chat.team.cancel')}
          </Button>
          <Button
            variant="accent"
            onClick={() => {
              setVoiceUnavailableDialogOpen(false);
              navigate('/app/settings');
            }}
          >
            {t('chat.team.openSettings')}
          </Button>
        </DialogFooter>
      </Dialog>
    </div>
  );
}
