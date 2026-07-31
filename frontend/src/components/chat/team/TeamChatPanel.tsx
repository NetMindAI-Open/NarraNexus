/**
 * @file_name: TeamChatPanel.tsx
 * @author:
 * @date: 2026-06-23
 * @description: Team group-chat surface. Renders one team's shared room:
 * a member bar on top (the user + member agents), then two panes — the message
 * timeline plus composer on the left, the standing member roster on the right.
 * The user posts into the room and @-mentioned agents reply; mentioning @all
 * addresses everyone.
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
import { CornerDownLeft, FileText, HelpCircle, Image as ImageIcon, Loader2, Mic, Plus, Settings2, Users2, X } from 'lucide-react';
import { RingAvatar } from '@/components/nm';
import { Button, Textarea, Markdown } from '@/components/ui';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/Dialog';
import { BusAttachmentList } from '../BusAttachmentList';
import { AudioRecorder } from '../AudioRecorder';
import { VoiceTranscript } from '../VoiceTranscript';
import { GuideRuleCards, TeamRoomHero } from './TeamRoomHero';
import { TeamMessageProcess } from './TeamMessageProcess';
import { TeamRosterPanel } from './TeamRosterPanel';
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

/**
 * IM-style "someone is typing" bubble — no stats, gone the moment the reply
 * lands. Clicking it opens that member's process detail in the roster (shared
 * highlight = same accent on both sides), so the transcript stays a transcript
 * and every number lives in exactly one place.
 */
function TypingIndicator({
  name,
  highlighted,
  onClick,
}: {
  name: string;
  highlighted: boolean;
  onClick: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex gap-3">
      <RingAvatar
        species="silicon"
        label={name.slice(0, 2)}
        size="sm"
        className="hidden shrink-0 md:inline-flex"
      />
      <div className="min-w-0 flex-1">
        <div className="mb-0.5 px-0.5 font-mono text-[10px] text-[var(--text-tertiary)]">{name}</div>
        <button
          type="button"
          onClick={onClick}
          aria-label={t('chat.team.typing', { name })}
          className="nm-bubble-ai inline-flex items-center gap-1 rounded-[var(--radius-lg)] px-3.5 py-2.5"
          style={{
            background: 'var(--color-silicon-soft)',
            border: highlighted
              ? '1px solid var(--color-silicon)'
              : '1px solid var(--color-silicon-hair)',
          }}
        >
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="h-1.5 w-1.5 animate-bounce rounded-full"
              style={{ background: 'var(--color-silicon)', animationDelay: `${i * 0.15}s` }}
            />
          ))}
        </button>
      </div>
    </div>
  );
}

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
  const [activity, setActivity] = useState<TeamMemberActivity[]>([]);
  const [leadAgentId, setLeadAgentId] = useState<string | null>(null);
  // 1s ticker (an epoch-ms stamp) so live durations advance between 3s polls.
  const [nowTick, setNowTick] = useState(0);
  // The roster's open member. It lives here, not in the roster, because the
  // transcript's typing bubble highlights the same selection — two owners of
  // one selection is how the two surfaces drift apart.
  const [rosterExpandedId, setRosterExpandedId] = useState<string | null>(null);
  // Narrow screens have no room for a standing column, so the roster becomes a
  // drawer over the transcript.
  const [mobileRosterOpen, setMobileRosterOpen] = useState(false);
  // The addressing rules on demand. They fill the empty room's hero; once the
  // transcript owns the space this popover is the only way back to them.
  const [guideOpen, setGuideOpen] = useState(false);
  const guideRef = useRef<HTMLDivElement | null>(null);
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
        setActivity(r.activity ?? []);
        setLeadAgentId(r.lead_agent_id ?? null);
      }
    } catch {
      // transient — the next tick retries
    }
  }, [teamId]);

  useEffect(() => {
    let alive = true;
    setMessages([]);
    setActivity([]);
    setLeadAgentId(null);
    refresh();
    const id = window.setInterval(() => { if (alive) refresh(); }, POLL_MS);
    return () => { alive = false; window.clearInterval(id); };
  }, [refresh]);

  // Tick every 1s while anything is in flight so elapsed / wait / silence
  // counters advance between the 3s polls. `stalled` and `queued` need it as
  // much as `running` — those two counters are the whole point of the state.
  const anyActive = activity.some((a) => a.status !== 'idle');
  useEffect(() => {
    if (!anyActive) return;
    const id = window.setInterval(() => setNowTick(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [anyActive]);

  // One clock for every duration on screen, so rows never disagree by a tick.
  const now = nowTick || Date.now();

  // A popover the user opened to read one thing must close on the next click
  // anywhere else — otherwise it sits over the transcript until re-clicked.
  useEffect(() => {
    if (!guideOpen) return;
    const onDown = (e: MouseEvent) => {
      if (guideRef.current && !guideRef.current.contains(e.target as Node)) setGuideOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [guideOpen]);

  const nameOf = useCallback(
    (agentId: string) => members.find((m) => m.agent_id === agentId)?.name || agentId,
    [members],
  );
  const leadName = leadAgentId ? nameOf(leadAgentId) : null;

  const toggleRoster = useCallback((agentId: string) => {
    setRosterExpandedId((cur) => (cur === agentId ? null : agentId));
  }, []);

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
          {members.map((m) => {
            // The default responder wears a dot: "who answers when I address
            // nobody" is otherwise invisible, and it is the single most useful
            // thing to know about a room you just opened.
            const isLead = m.agent_id === leadAgentId;
            return (
              <button
                key={m.agent_id}
                type="button"
                onClick={() => insertMention(m.name || m.agent_id)}
                title={
                  isLead
                    ? t('chat.team.leadTitle', { name: m.name || m.agent_id })
                    : `@${m.name || m.agent_id}`
                }
                className="relative shrink-0 rounded-full transition-transform hover:-translate-y-0.5"
              >
                <RingAvatar species="silicon" label={(m.name || m.agent_id).slice(0, 2)} size="sm" />
                {isLead && (
                  <span
                    className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full border border-[var(--nm-paper)]"
                    style={{ background: accent }}
                    aria-hidden
                  />
                )}
              </button>
            );
          })}
          {members.length === 0 && (
            <span className="text-xs text-[var(--text-tertiary)]">{t('chat.team.noAgents')}</span>
          )}
        </div>

        {/* Roster drawer toggle — narrow screens have no standing column. */}
        <button
          type="button"
          onClick={() => setMobileRosterOpen((v) => !v)}
          title={t('chat.team.roster.title')}
          aria-label={t('chat.team.roster.title')}
          className="ml-auto shrink-0 flex h-7 w-7 items-center justify-center rounded-[var(--radius-xs)] text-[var(--text-secondary)] transition-colors hover:bg-[var(--nm-paper-warm)] hover:text-[var(--color-carbon)] md:hidden"
        >
          <Users2 className="w-3.5 h-3.5" />
        </button>

        {/* The addressing rules, on demand. The empty room's hero states them
            once; after the first message this is the only way back to them. */}
        <div className="relative ml-auto shrink-0" ref={guideRef}>
          <button
            type="button"
            onClick={() => setGuideOpen((v) => !v)}
            aria-expanded={guideOpen}
            aria-label={t('chat.team.guide.title')}
            className="flex h-7 w-7 items-center justify-center rounded-[var(--radius-xs)] text-[var(--text-secondary)] transition-colors hover:bg-[var(--nm-paper-warm)] hover:text-[var(--color-carbon)]"
          >
            <HelpCircle className="w-3.5 h-3.5" />
          </button>
          {guideOpen && (
            <div className="absolute right-0 top-full z-30 mt-1 w-72 rounded-[var(--radius-md)] border border-[var(--rule)] bg-[var(--nm-paper)] p-3 shadow-lg">
              <GuideRuleCards leadName={leadName} accent={accent} />
            </div>
          )}
        </div>

        {/* Team settings (detail page). */}
        <button
          type="button"
          onClick={() => navigate(`/app/teams/${teamId}`)}
          title={t('chat.team.teamSettings')}
          aria-label={t('chat.team.teamSettings')}
          className="shrink-0 flex h-7 w-7 items-center justify-center rounded-[var(--radius-xs)] text-[var(--text-secondary)] transition-colors hover:bg-[var(--nm-paper-warm)] hover:text-[var(--color-carbon)]"
        >
          <Settings2 className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Two panes: the conversation on the left, the standing roster on the
          right. The roster replaced the folded activity console — "what is
          each teammate doing" is the room's primary question, so it is chrome
          rather than something the user has to expand to find out. */}
      <div className="relative flex flex-1 min-h-0">
        <div className="flex min-w-0 flex-1 flex-col min-h-0">
          {/* Timeline */}
          <div className="flex-1 min-h-0 overflow-y-auto px-5 py-4">
            {messages.length === 0 ? (
              <TeamRoomHero
                teamName={team.team.name}
                memberNames={members.map((m) => m.name || m.agent_id)}
                leadName={leadName}
                accent={accent}
              />
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
                          {/* This turn's full process — single-chat parity.
                              Only agent replies whose turn was recorded carry
                              an event_id (legacy rows degrade to no button). */}
                          {!mine && m.event_id && (
                            <TeamMessageProcess agentId={m.from_agent} eventId={m.event_id} />
                          )}
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

                {/* Someone is working right now — the transcript says only that,
                    and only while it is true. Everything measurable about the run
                    (elapsed, phases, tools) belongs to the roster; a finished turn
                    leaves the flow clean instead of piling up stale traces. */}
                {activity
                  .filter((a) => a.status === 'running')
                  .map((a) => (
                    <TypingIndicator
                      key={`typing-${a.agent_id}`}
                      name={nameOf(a.agent_id)}
                      highlighted={rosterExpandedId === a.agent_id}
                      onClick={() => {
                        toggleRoster(a.agent_id);
                        setMobileRosterOpen(true);
                      }}
                    />
                  ))}
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
        </div>

        <TeamRosterPanel
          members={members}
          activity={activity}
          leadAgentId={leadAgentId}
          now={now}
          expandedId={rosterExpandedId}
          onToggle={toggleRoster}
          accent={accent}
          onOpenSettings={() => navigate(`/app/teams/${teamId}`)}
          className="hidden md:flex"
        />

        {/* Narrow screens: the same rows, over the transcript. The drawer
            keeps the roster's own breathing width (256px ↔ 430px capped
            at 92vw) — a fixed width here would undo the expansion. */}
        {mobileRosterOpen && (
          <TeamRosterPanel
            members={members}
            activity={activity}
            leadAgentId={leadAgentId}
            now={now}
            expandedId={rosterExpandedId}
            onToggle={toggleRoster}
            accent={accent}
            onOpenSettings={() => navigate(`/app/teams/${teamId}`)}
            className="absolute inset-y-0 right-0 z-20 flex border-l border-[var(--rule)] bg-[var(--nm-paper)] shadow-lg md:hidden"
          />
        )}
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
