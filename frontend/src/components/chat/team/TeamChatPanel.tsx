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

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { CornerDownLeft, FileText, FolderOpen, HelpCircle, Image as ImageIcon, Loader2, Mic, Plus, Settings2, Users2, X } from 'lucide-react';
import { RingAvatar } from '@/components/nm';
import { Button, Textarea } from '@/components/ui';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/Dialog';
import { AudioRecorder } from '../AudioRecorder';
import { VoiceTranscript } from '../VoiceTranscript';
import { GuideRuleCards, TeamRoomHero } from './TeamRoomHero';
import { TeamRosterPanel } from './TeamRosterPanel';
import { teamDrawerCategories, teamTabLabelKey, type TeamTabId } from './teamTabs';
import { BookmarkDrawer } from '@/components/bookmarks/BookmarkDrawer';
import { ResizableDivider } from '@/components/layout/ResizableDivider';
import { usePinnedDrawer } from '@/hooks/usePinnedDrawer';
import { useIsMobile } from '@/hooks/useMediaQuery';
import { TeamTranscript } from './TeamTranscript';
import { beforeCursor, mergeTeamMessages, sinceCursor } from './mergeTeamMessages';
import { isNearBottom, isNearTop } from '@/lib/scrollStickiness';
import { latestTeamMessageMs, markTeamRead } from '@/lib/unread';
import { getTeamDraft, setTeamDraft } from '@/lib/chatDrafts';
import { matchMembers, mentionTokens } from './mentionPattern';
import { TeamSystemLine } from './TeamSystemLine';
import { TeamMessageFooter } from './TeamMessageFooter';
import { TeamMessageProcess } from './TeamMessageProcess';
import { TeamWorkspacePanel } from './TeamWorkspacePanel';
import { ArtifactsGlyph } from '@/components/bookmarks';
import { TeamManagePanel } from './TeamManagePanel';
import type { Artifact, TeamFile } from '@/types/artifact';
import { useTeamsStore, useConfigStore, useChatStore } from '@/stores';
import { api } from '@/lib/api';
// No `formatTime` here on purpose: it arrived with the liveness work, which
// used it in the inline message loop this file replaced with <TeamTranscript>.
// The per-message timestamp now lives in TeamMessageFooter, which imports it
// itself.
import { cn } from '@/lib/utils';
import { STATUS_TONES, elapsedSince } from '@/lib/teamActivity';
import type { AgentInfo } from '@/types';
import type { TeamBulletin, TeamChatMessage, TeamMemberActivity } from '@/types/teams';
import type { BusAttachment } from '@/types';

interface TeamChatPanelProps {
  teamId: string;
}

/** A mention-dropdown option: the @all broadcast, or a specific teammate. */
type MentionOption = { kind: 'all' } | { kind: 'agent'; agent: AgentInfo };

const POLL_MS = 3000;

/** Same as the private chat's composer: long enough to coalesce a burst of
 *  keystrokes, short enough that a crash loses at most a word. */
const DRAFT_PERSIST_DEBOUNCE_MS = 400;

/**
 * IM-style sign-of-life bubble — no stats, gone the moment the member goes
 * idle. Clicking it opens that member's process detail in the roster (shared
 * highlight = same accent on both sides), so the transcript stays a transcript
 * and every number lives in exactly one place.
 *
 * Renders for three live states, not just `running`:
 *
 *   running  animated dots, "is typing" — unchanged
 *   queued   still dots, dimmed, plus how long it has been waiting
 *   stalled  plus how long there has been no signal
 *
 * Why `queued` belongs here at all: it is derived from pending messages on the
 * GET, so it is true within one 3s poll of the message landing — it does not
 * wait for the poll interval, a worker slot and Step 0 the way `running` does.
 * Showing only `running` is what left the conversation blank while the roster
 * already knew someone was up, which is the "dead room" the PRD is about.
 *
 * `idle` still renders nothing: a FINISHED turn leaves no trace in the flow,
 * its record lives one click away in the roster. That rule is unchanged — it
 * was never "only running may show", it was "finished leaves nothing".
 *
 * Colour and copy both come from `STATUS_TONES` — this is the FOURTH surface
 * rendering these states, and `teamActivity.ts` exists precisely so they cannot
 * disagree about what "stalled" looks like. Hard-coding them here (the first
 * version did) put `stalled` at warning-amber in the transcript while the
 * roster drew it error-red for the same member at the same moment: two
 * different severities, one state. Softening is applied ON TOP of the semantic
 * colour (see `opacity` below), never by swapping it for another one.
 */
function LivenessIndicator({
  name,
  status,
  detail,
  highlighted,
  onClick,
}: {
  name: string;
  status: 'running' | 'queued' | 'stalled';
  detail?: string;
  highlighted: boolean;
  onClick: () => void;
}) {
  const { t } = useTranslation();

  const tone = STATUS_TONES[status];

  // `running` keeps its exact original label: it is the accessible name the
  // room has always had for this, and it is what existing tests target.
  const label =
    status === 'running'
      ? t('chat.team.typing', { name })
      : `${name} · ${t(tone.labelKey)}`;

  const accent = tone.color;

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
          title={label}
          aria-label={label}
          className="nm-bubble-ai inline-flex items-center gap-2 rounded-[var(--radius-lg)] px-3.5 py-2.5"
          style={{
            background:
              status === 'running'
                ? 'var(--color-silicon-soft)'
                : `color-mix(in srgb, ${accent} 8%, transparent)`,
            border: highlighted
              ? `1px solid ${accent}`
              : status === 'running'
                ? '1px solid var(--color-silicon-hair)'
                : `1px solid color-mix(in srgb, ${accent} 35%, transparent)`,
            // A queued member is not working yet; the bubble should read as
            // present-but-not-active rather than compete with a live turn.
            opacity: status === 'queued' ? 0.72 : 1,
          }}
        >
          <span className="inline-flex items-center gap-1">
            {[0, 1, 2].map((i) => (
              <span
                key={i}
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  // Only a turn that is actually running animates. A queued or
                  // stalled member bouncing would say "working" — the exact
                  // misreading the four states exist to prevent.
                  status === 'running' && 'animate-bounce',
                )}
                style={{
                  background: accent,
                  animationDelay: `${i * 0.15}s`,
                  opacity: status === 'running' ? 1 : 0.55,
                }}
              />
            ))}
          </span>
          {detail && (
            <span className="font-mono text-[10px] text-[var(--text-tertiary)]">{detail}</span>
          )}
        </button>
      </div>
    </div>
  );
}

/** The duration line under a liveness bubble, or nothing.
 *
 * `elapsedSince` returns '' when the timestamp is missing (its documented
 * contract), and "waiting " with a blank tail reads like a truncated string
 * rather than a missing value. No duration => no detail line.
 */
function livenessDetail(
  t: (k: string, v?: Record<string, unknown>) => string,
  a: TeamMemberActivity,
  now: number,
): string | undefined {
  if (a.status === 'queued') {
    const d = elapsedSince(a.queued_since, now);
    return d ? t('chat.team.activity.waitingFor', { duration: d }) : undefined;
  }
  if (a.status === 'stalled') {
    const d = elapsedSince(a.last_signal_at, now);
    return d ? t('chat.team.activity.silentFor', { duration: d }) : undefined;
  }
  return undefined;
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

  // agent_id → display name, memoised ONCE and shared by every consumer.
  //
  // Built inline in the JSX until 2026-08-14, which quietly defeated a memo
  // three components down: a fresh object each render → a fresh `nameSet` in
  // every bubble → a fresh rehype plugin array → `Markdown`'s shallow-equality
  // memo misses → remark/rehype re-parse the whole body. This panel renders at
  // least once a second (the 1s ticker for live durations) and once per
  // keystroke (the composer's text lives here), so a 200-message room was
  // re-parsing 200 markdown bodies every second, and again on every character
  // typed. Before mentions moved from a string rewrite into a plugin, the memo
  // matched on VALUE and held; the move swapped that for reference equality
  // without anyone making the reference stable.
  const memberNameMap = useMemo(
    () => Object.fromEntries(members.map((m) => [m.agent_id, m.name || m.agent_id])),
    [members],
  );

  // Seeded from the stored draft: the room is a place you leave, so what was
  // half-typed has to still be here when you come back.
  const [text, setText] = useState(() => getTeamDraft(teamId));
  const textRef = useRef(text);
  textRef.current = text;
  // What just went wrong in the composer. A failed send used to restore the
  // text and say nothing, which is indistinguishable from the Enter key not
  // registering — so the user retypes, or sends twice.
  const [composerError, setComposerError] = useState<string | null>(null);
  // IME state. Enter is how a Pinyin/Kana candidate is ACCEPTED; sending on it
  // makes the composer unusable for the languages this project is written in.
  // Some IMEs fire compositionend before that final keydown, hence the grace
  // window as well as the flag — the private chat's Composer learned both.
  const isComposingRef = useRef(false);
  const compositionEndTimeRef = useRef(0);
  const [messages, setMessages] = useState<TeamChatMessage[]>([]);
  // Read by the poll without making `refresh` depend on the transcript: a
  // changing dependency would tear down and recreate the interval on every
  // message, which is how a 3s poll becomes a much faster one.
  // useLayoutEffect, NOT useEffect: passive effects flush asynchronously —
  // possibly after paint AND after user events — so a scroll landing in
  // that window read a stale (empty) ref, loadOlder's !cursor guard bailed
  // silently, and the top-of-history fetch simply didn't happen (no retry;
  // the user has to scroll again). Layout effects commit synchronously,
  // before any event can observe the DOM of this render.
  const messagesRef = useRef<TeamChatMessage[]>([]);
  useLayoutEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  // Everything on screen has been seen — including the platform's own lines,
  // which the SERVER excludes when deciding whether a room is worth returning
  // to. The two rules differ on purpose: the server answers "is this worth a
  // mark", this answers "what has the user looked at", and a line rendered in
  // front of them has been looked at whoever wrote it. Marking less than what is
  // displayed would leave a room that only narrated itself permanently marked.
  //
  // Monotonic, so it composes with the sidebar's own marking (which can only see
  // the list response) without either being able to undo the other.
  useEffect(() => {
    if (!teamId) return;
    markTeamRead(teamId, latestTeamMessageMs(messages));
  }, [teamId, messages]);

  // Which room the text in the composer BELONGS to. `teamId` and `text` update
  // on different commits — a route change re-renders with the new room and the
  // old text still in state — so anything that persists the draft has to know
  // which of the two it is currently holding. Without this the first save after
  // a room switch files the previous room's words under the new room's name.
  const draftRoomRef = useRef(teamId);

  // Debounced persistence.
  //
  // On the one commit where the room has changed and the text has not yet
  // caught up, this schedules a write of the OLD text under the NEW room. It
  // cannot land: the switch effect below sets the text in the same commit, and
  // the resulting re-render clears the timer first. The single case where React
  // skips that re-render is when the two strings are already equal — and then
  // the write is a no-op by definition. A guard stood here until a mutation
  // showed nothing could observe it.
  useEffect(() => {
    const id = window.setTimeout(() => setTeamDraft(teamId, text), DRAFT_PERSIST_DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [teamId, text]);

  // Switching rooms: flush what was typed into the room being LEFT (textRef
  // still holds it here), then load the room being entered.
  useEffect(() => {
    const leaving = draftRoomRef.current;
    if (leaving === teamId) return;
    setTeamDraft(leaving, textRef.current);
    draftRoomRef.current = teamId;
    setText(getTeamDraft(teamId));
  }, [teamId]);

  // Unmounting: same flush, for navigating away rather than sideways. Text
  // typed inside the debounce window would otherwise be lost by exactly the
  // action that makes a draft worth having.
  useEffect(() => {
    return () => setTeamDraft(draftRoomRef.current, textRef.current);
  }, []);

  // Workspace data lives HERE, not in the panel: a chip under a message and
  // the panel's own list must agree on what is open, so one component owns the
  // state — and it has to be the one both of them are inside.
  const [wsArtifacts, setWsArtifacts] = useState<Artifact[]>([]);
  const [wsFiles, setWsFiles] = useState<TeamFile[]>([]);
  const [wsTurns, setWsTurns] = useState<Record<string, string[]>>({});
  const [wsLoading, setWsLoading] = useState(false);
  const [wsError, setWsError] = useState<string | null>(null);
  const [wsSelected, setWsSelected] = useState<string | null>(null);
  // The room's right side IS the single-chat right side: one shared drawer
  // (same pin/width preferences, same title switcher) hosting the team's
  // panels — members, artifacts, shared files. Which panel is open lives
  // in drawerTab below; its default is decided there.
  const isMobile = useIsMobile();
  const {
    pinned: drawerPinned,
    setPinned: setDrawerPinned,
    effectiveWidth: drawerWidth,
    colRef: drawerColRef,
    handleResize: handleDrawerResize,
    handleResizeEnd: handleDrawerResizeEnd,
  } = usePinnedDrawer();
  // One-shot initializer, deliberately: the members panel opens by default
  // only where it is a pinned column (desktop + the user's shared pin
  // preference ON). Auto-opening a TRANSIENT drawer would greet an
  // unpinned user with a full-viewport dismiss backdrop that eats their
  // first click. Do not derive from `pinned` or sync in an effect — an
  // unpin inside the room must not close/reopen the panel.
  const [drawerTab, setDrawerTab] = useState<TeamTabId | null>(() =>
    !isMobile && drawerPinned ? 'members' : null,
  );
  const toggleDrawerTab = (id: TeamTabId) =>
    setDrawerTab((prev) => (prev === id ? null : id));
  const workspaceRefreshTick = useChatStore((s) => s.workspaceRefreshTick);
  // The bulletin lives here, like the workspace: a change posts a system line
  // into the transcript, so the transcript and the panel must agree on when
  // something changed, and one component has to own that.
  const [bulletin, setBulletin] = useState<TeamBulletin | null>(null);
  const [bulletinLoading, setBulletinLoading] = useState(false);
  const [bulletinError, setBulletinError] = useState<string | null>(null);
  const [activity, setActivity] = useState<TeamMemberActivity[]>([]);
  const [leadAgentId, setLeadAgentId] = useState<string | null>(null);
  // 1s ticker (an epoch-ms stamp) so live durations advance between 3s polls.
  const [nowTick, setNowTick] = useState(0);
  // The roster's open member. It lives here, not in the roster, because the
  // transcript's typing bubble highlights the same selection — two owners of
  // one selection is how the two surfaces drift apart.
  const [rosterExpandedId, setRosterExpandedId] = useState<string | null>(null);
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
  const notePatrol = useTeamsStore((s) => s.notePatrol);
  const refresh = useCallback(async () => {
    try {
      // Incremental: `since` has existed end to end for a long time and the
      // panel simply never sent it, refetching all 200 messages every 3s. The
      // full refetch was idempotent by construction (`setMessages(all)`), so
      // the merge has to earn that back — see `mergeTeamMessages`, which is
      // append-only-with-dedup because `bus_messages` is never updated in place
      // (asserted in tests/message_bus/test_team_message_segments.py).
      const cursor = sinceCursor(messagesRef.current);
      const r = await api.getTeamChat(teamId, cursor);
      if (r.success) {
        setMessages((prev) =>
          cursor ? mergeTeamMessages(prev, r.messages) : r.messages,
        );
        setActivity(r.activity ?? []);
        setLeadAgentId(r.lead_agent_id ?? null);
        if (typeof r.patrol_enabled === 'boolean') notePatrol(teamId, r.patrol_enabled);
      }
    } catch {
      // transient — the next tick retries
    }
  }, [teamId, notePatrol]);

  // Paging BACK. `hasMoreRef` is a ref, not state: the scroll handler reads it
  // on every scroll event, and a state read there would be a render behind.
  const loadingOlderRef = useRef(false);
  const hasMoreRef = useRef(true);
  // The same fact as the ref, for the reader rather than the guard: scrolling
  // to the top and seeing nothing happen looks exactly like having reached the
  // beginning of the room.
  const [loadingOlder, setLoadingOlder] = useState(false);

  /**
   * Fetch the page above the transcript and prepend it.
   *
   * The scroll position is restored by hand. Prepending moves everything the
   * reader is looking at DOWN by exactly the height of what was added, so
   * leaving `scrollTop` alone teleports them away from the message that made
   * them scroll up — the one thing a "load more" must not do.
   */
  const loadOlder = useCallback(async () => {
    if (loadingOlderRef.current || !hasMoreRef.current) return;
    const cursor = beforeCursor(messagesRef.current);
    // Nothing on screen means no page above it. Asking anyway would refetch the
    // newest page under a cursor and merge it into itself.
    if (!cursor) return;
    loadingOlderRef.current = true;
    setLoadingOlder(true);
    const el = scrollRef.current;
    const heightBefore = el?.scrollHeight ?? 0;
    const topBefore = el?.scrollTop ?? 0;
    try {
      const r = await api.getTeamChat(teamId, undefined, cursor);
      if (!r.success) return;
      if (!r.messages.length) {
        // The top of the history. Without latching this the room re-asks on
        // every scroll event for the rest of the session.
        hasMoreRef.current = false;
        return;
      }
      setMessages((prev) => mergeTeamMessages(prev, r.messages));
      requestAnimationFrame(() => {
        const node = scrollRef.current;
        if (!node) return;
        node.scrollTop = topBefore + (node.scrollHeight - heightBefore);
      });
    } catch {
      // transient — the next scroll to the top retries
    } finally {
      loadingOlderRef.current = false;
      setLoadingOlder(false);
    }
  }, [teamId]);

  useEffect(() => {
    let alive = true;
    setMessages([]);
    // Cleared HERE as well, not just through the state: `refresh` and
    // `loadOlder` read the transcript through this ref, and it is synced by an
    // effect that has not run yet. Leaving it would fetch the new room with a
    // cursor taken from the PREVIOUS room's conversation — everything older
    // than that timestamp would never arrive, and if the old room's last
    // message was the newer of the two, the new room would render empty.
    messagesRef.current = [];
    // A new room has its own history; inheriting "the top was reached" would
    // make the second room silently refuse to page back at all.
    hasMoreRef.current = true;
    loadingOlderRef.current = false;
    setLoadingOlder(false);
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

  // Designate the team lead straight from the roster, where the badge is
  // shown — the setter used to live only in the buried "default responder"
  // select of the Edit-Team modal. Optimistic: reflect it immediately, roll
  // back if the PATCH fails. A later activity poll reconciles either way.
  //
  // The in-flight ref rejects a second click before the first PATCH resolves:
  // without it the second call's `prev` snapshot would be the first click's
  // optimistic value, so a failure would roll back to the wrong (intermediate)
  // lead instead of the original.
  const settingLeadRef = useRef(false);
  const handleSetLead = useCallback(
    async (agentId: string) => {
      if (settingLeadRef.current) return;
      settingLeadRef.current = true;
      const prev = leadAgentId;
      setLeadAgentId(agentId);
      try {
        const res = await api.updateTeam(teamId, { lead_agent_id: agentId });
        if (!res.success) setLeadAgentId(prev);
      } catch {
        setLeadAgentId(prev);
      } finally {
        settingLeadRef.current = false;
      }
    },
    [teamId, leadAgentId],
  );

  const toggleRoster = useCallback((agentId: string) => {
    setRosterExpandedId((cur) => (cur === agentId ? null : agentId));
  }, []);

  // Follow the transcript only while the reader is already at the bottom.
  // Unconditional scrolling meant a user scrolled up to read something from two
  // minutes ago was yanked down every few seconds — the room was least readable
  // exactly when it was busiest.
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickRef = useRef(true);
  useEffect(() => {
    if (!stickRef.current) return;
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

  /** Resolve the @tokens in the composed text to agent_ids and/or "@all".
   *
   *  Tokenised by the shared `mentionTokens` — the third hand-copied regex in
   *  this folder lived here, and it is the one that decides who is actually
   *  WOKEN. Highlighting and waking disagreeing is the worst version of this
   *  bug: the reader sees three names lit and two teammates answer, with no way
   *  to tell which half is wrong.
   *
   *  The resolution is loose — first names and prefixes count, because someone
   *  typing `@ana` for "Ana Silva" means her — and the renderers now use the
   *  same rule through `matchMembers`. They used to be stricter, which meant a
   *  teammate could be woken while the room drew their name as ordinary text. */
  const resolveMentions = (value: string): string[] => {
    const tokens = mentionTokens(value);
    if (tokens.size === 0) return [];
    if (tokens.has('all') || tokens.has('everyone')) return ['@all'];
    // Matched by name through the shared rule, then mapped back to ids. The
    // matching itself is NOT reimplemented here: this decides who is woken and
    // `isAddressed` decides who is highlighted, and the two disagreeing is the
    // failure this folder repeatedly calls worse than no highlight at all.
    // A display name can belong to more than one member — two clones of an
    // agent, or two that kept a default name. Keying a Map by name would drop
    // all but the last, so `@Researcher` would wake one of them while the room
    // highlighted the word for both: a highlight promising a wake that does not
    // happen, which is the failure this whole rule was unified to prevent. The
    // server iterates members, not names, and so does this.
    const byName = new Map<string, string[]>();
    for (const m of members) {
      const nm = m.name || m.agent_id;
      const ids = byName.get(nm);
      if (ids) ids.push(m.agent_id);
      else byName.set(nm, [m.agent_id]);
    }
    return matchMembers(tokens, byName.keys()).flatMap((name) => byName.get(name) ?? []);
  };

  const handlePickFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    setComposerError(null);
    try {
      for (const file of Array.from(files)) {
        const res = await api.uploadTeamChatAttachment(teamId, file);
        if (res.success && res.attachment) {
          setPending((prev) => [...prev, res.attachment!]);
        } else {
          // A refusal is not an exception, and "no chip appeared" looks exactly
          // like an upload still in flight. Name the file: with several
          // selected, which one failed is the whole question.
          setComposerError(t('chat.team.uploadFailed', { name: file.name }));
        }
      }
    } catch {
      setComposerError(t('chat.team.uploadFailedGeneric'));
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
      // Capture failures are the AudioRecorder's own onError; this is the
      // UPLOAD failing, which nothing else reports — and a voice memo that
      // vanishes with no message is the worst version of this bug, because the
      // recording cannot be retyped.
      setComposerError(t('chat.team.uploadFailedGeneric'));
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
    setTeamDraft(teamId, '');
    setPending([]);
    closeMention();
    // A stale error sitting next to a message that did send is its own lie.
    setComposerError(null);
    setSending(true);
    try {
      await api.sendTeamChat(teamId, body, mentions, attachments);
      await refresh();
    } catch {
      // Restore the draft + attachments so nothing is lost — and SAY SO.
      // Restoring silently is indistinguishable from the Enter key never
      // having registered, so the user retypes it or sends it twice.
      setText(body);
      setTeamDraft(teamId, body);
      setPending(attachments);
      setComposerError(t('chat.team.sendFailed'));
    } finally {
      setSending(false);
    }
  };

  const reloadBulletin = useCallback(async () => {
    setBulletinLoading(true);
    try {
      const b = await api.getTeamBulletin(teamId);
      setBulletin(b);
      setBulletinError(null);
    } catch (e) {
      setBulletinError(e instanceof Error ? e.message : 'Failed to load bulletin');
    } finally {
      setBulletinLoading(false);
    }
  }, [teamId]);

  useEffect(() => {
    void reloadBulletin();
    // Also on the workspace tick: clearing team data can take the bulletin with
    // it, and a panel still listing deleted rules is worse than an empty one.
  }, [reloadBulletin, workspaceRefreshTick]);

  useEffect(() => {
    let alive = true;
    void (async () => {
      setWsLoading(true);
      try {
        const [a, f, turns] = await Promise.all([
          api.listTeamArtifacts(teamId),
          api.listTeamFiles(teamId),
          api.listTeamArtifactTurns(teamId),
        ]);
        if (!alive) return;
        setWsArtifacts(a);
        setWsFiles(f);
        setWsTurns(turns);
        setWsError(null);
      } catch (e) {
        if (alive) setWsError(e instanceof Error ? e.message : 'Failed to load workspace');
      } finally {
        if (alive) setWsLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
    // Keyed on message count: a turn that registered something has just landed
    // in the transcript, so this is the cheapest honest signal that the
    // workspace may have changed. That signal misses one case — a wipe of the
    // team's files, which empties this panel while leaving the transcript
    // exactly as it was — so the store's explicit tick covers it.
  }, [teamId, messages.length, workspaceRefreshTick]);

  /** After a clear from the management tab: drop what the server dropped.
   *  Emptying `messages` is the whole chat-side remedy — the next 3s poll then
   *  refetches from scratch (no `since` cursor once the list is empty). The
   *  workspace and bulletin reload off the store's tick. */
  const requestWorkspaceRefresh = useChatStore((s) => s.requestWorkspaceRefresh);
  const handleCleared = (scopes: { chat: boolean; files: boolean; bulletin: boolean }) => {
    if (scopes.chat) {
      setMessages([]);
      hasMoreRef.current = false;
    }
    if (scopes.files || scopes.bulletin) requestWorkspaceRefresh();
  };

  /** Returns the server's error text, or null on success. */
  const bulletinAction = async (
    call: () => Promise<{ success: boolean; error?: string }>,
  ): Promise<string | null> => {
    try {
      const res = await call();
      if (!res.success) return res.error || 'Failed';
      await reloadBulletin();
      // The change posts a system line into the room; pull it in now rather
      // than waiting up to a poll interval for the transcript to catch up.
      await refresh();
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : 'Failed';
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
    <div className="flex h-full min-h-0 min-w-0">
      {/* min-w-0 here is load-bearing on a phone-width viewport: this is a
          flex-row item inside MainLayout's `<main>`, and without it the
          browser's default flex auto-min-size lets any non-wrapping
          descendant (long team/agent names, the roster strip, composer
          chrome) stretch this column past the viewport. MainLayout's own
          wrapper clips that overflow with `overflow-hidden`, so the excess
          doesn't scroll into view — it just pushes the right-anchored send
          button (`absolute right-2` on the composer) outside the visible,
          clipped area. See GitHub #131. */}
      <div
        data-testid="team-room-shell"
        className="flex h-full flex-1 min-w-0 flex-col min-h-0"
      >
      {/* Member bar — team identity + the roster of agents in this room. */}
      <div className="shrink-0 flex flex-wrap items-center gap-x-3 gap-y-2 px-5 py-2.5 border-b border-[var(--nm-hairline)]">
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
        <div className="flex min-w-0 items-center gap-1.5 ml-2 overflow-x-auto">
          <span title={t('chat.team.youTitle', { name: userLabel })} className="shrink-0">
            <RingAvatar species="carbon" label={(userLabel || '?').slice(0, 2)} size="sm" />
          </span>
          {members.length > 0 && (
            <span className="w-px h-5 bg-[var(--nm-hairline)] mx-0.5 shrink-0" aria-hidden />
          )}
          {members.map((m) => {
            // The team lead wears a dot: "who answers when I address
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

        {/* Members panel toggle — the roster is a drawer panel now, on
            every viewport (the standing column it replaces opened by
            default; the drawer starts on 'members' on desktop). */}
        <button
          type="button"
          onClick={() => toggleDrawerTab('members')}
          aria-pressed={drawerTab === 'members'}
          data-testid="members-toggle"
          title={t('chat.team.roster.title')}
          aria-label={t('chat.team.roster.title')}
          className={cn(
            'shrink-0 flex h-7 items-center gap-1 rounded-[var(--radius-xs)] px-1.5 text-[var(--text-secondary)] transition-colors hover:bg-[var(--nm-paper-warm)] hover:text-[var(--color-carbon)]',
            drawerTab === 'members' && 'bg-[var(--nm-paper-warm)] text-[var(--color-carbon)]',
          )}
        >
          <Users2 className="w-3.5 h-3.5" />
          <span className="text-[11px]">{t('chat.team.roster.title')}</span>
        </button>

        {/* The addressing rules, on demand. The empty room's hero states them
            once; after the first message this is the only way back to them. */}
        {/* Right-side controls as one wrap-group: on desktop they sit at the
            end of the bar; on a phone the whole group drops to a second line
            (member bar is flex-wrap) and wraps within itself, so no toggle is
            ever clipped by MainLayout's overflow-hidden. */}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 ml-auto">
        <div className="relative shrink-0" ref={guideRef}>
          <button
            type="button"
            onClick={() => setGuideOpen((v) => !v)}
            aria-expanded={guideOpen}
            title={t('chat.team.guide.title')}
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

        {/* Workspace drawer (artifacts + shared files) — same entry pattern as
            the single-chat header's artifacts button; the standing w-72 column
            it replaces couldn't actually display an artifact. */}
        <button
          type="button"
          onClick={() => toggleDrawerTab('artifacts')}
          aria-pressed={drawerTab === 'artifacts'}
          data-testid="artifacts-toggle"
          title={t('rail.artifacts')}
          aria-label={t('rail.artifacts')}
          className={cn(
            'shrink-0 flex h-7 items-center gap-1 rounded-[var(--radius-xs)] px-1.5 text-[var(--text-secondary)] transition-colors hover:bg-[var(--nm-paper-warm)] hover:text-[var(--color-carbon)]',
            drawerTab === 'artifacts' && 'bg-[var(--nm-paper-warm)] text-[var(--color-carbon)]',
          )}
        >
          <ArtifactsGlyph className="w-3.5 h-3.5" strokeWidth={1.8} />
          <span className="text-[11px]">{t('rail.artifacts')}</span>
          {wsArtifacts.length > 0 && (
            <span className="text-[10px] font-mono">{wsArtifacts.length}</span>
          )}
        </button>

        {/* Shared files — its own entry beside the drawer's title switcher (both
            coexist). A file the team shared is invisible unless something
            advertises it, so the count rides here too. */}
        <button
          type="button"
          onClick={() => toggleDrawerTab('files')}
          aria-pressed={drawerTab === 'files'}
          data-testid="files-toggle"
          title={t('chat.team.workspace.tabFiles')}
          aria-label={t('chat.team.workspace.tabFiles')}
          className={cn(
            'shrink-0 flex h-7 items-center gap-1 rounded-[var(--radius-xs)] px-1.5 text-[var(--text-secondary)] transition-colors hover:bg-[var(--nm-paper-warm)] hover:text-[var(--color-carbon)]',
            drawerTab === 'files' && 'bg-[var(--nm-paper-warm)] text-[var(--color-carbon)]',
          )}
        >
          <FolderOpen className="w-3.5 h-3.5" />
          <span className="text-[11px]">{t('chat.team.workspace.tabFiles')}</span>
          {wsFiles.length > 0 && (
            <span className="text-[10px] font-mono">{wsFiles.length}</span>
          )}
        </button>

        {/* Team management — bulletin, lead, patrol, members, clear, delete —
            as a drawer tab like the others. It replaces the settings gear
            (which led to a page with none of those controls) and the bulletin
            button at the end of this bar (which was where nobody looked). */}
        <button
          type="button"
          onClick={() => toggleDrawerTab('manage')}
          aria-pressed={drawerTab === 'manage'}
          data-testid="manage-toggle"
          title={t('chat.team.manage.title')}
          aria-label={t('chat.team.manage.title')}
          className={cn(
            'shrink-0 flex h-7 items-center gap-1 rounded-[var(--radius-xs)] px-1.5 text-[var(--text-secondary)] transition-colors hover:bg-[var(--nm-paper-warm)] hover:text-[var(--color-carbon)]',
            drawerTab === 'manage' && 'bg-[var(--nm-paper-warm)] text-[var(--color-carbon)]',
          )}
        >
          <Settings2 className="w-3.5 h-3.5" />
          <span className="text-[11px]">{t('chat.team.manage.title')}</span>
          {(bulletin?.usage.entry_count ?? 0) > 0 && (
            <span className="text-[10px] font-mono" data-testid="bulletin-count">
              {bulletin?.usage.entry_count}
            </span>
          )}
        </button>
        </div>
      </div>

      {/* Two panes: the conversation on the left, the standing roster on the
          right. The roster replaced the folded activity console — "what is
          each teammate doing" is the room's primary question, so it is chrome
          rather than something the user has to expand to find out. */}
      <div className="relative flex flex-1 min-h-0">
        <div className="flex min-w-0 flex-1 flex-col min-h-0">
          {/* Timeline */}
          <div
            ref={scrollRef}
            data-testid="team-transcript-scroll"
            onScroll={(e) => {
              // The reader's position decides whether new messages may move it.
              stickRef.current = isNearBottom(e.currentTarget);
              // ...and reaching the top is the request for older ones. Scroll
              // events arrive in bursts; loadOlder is idempotent under that.
              if (isNearTop(e.currentTarget)) void loadOlder();
            }}
            className="flex-1 min-h-0 overflow-y-auto px-5 py-4"
          >
            {loadingOlder && (
              <div
                data-testid="loading-older"
                className="flex items-center justify-center gap-2 py-2 text-xs text-[var(--text-tertiary)]"
              >
                <Loader2 className="w-3 h-3 animate-spin" />
                {t('chat.team.loadingOlder')}
              </div>
            )}
            {messages.length === 0 ? (
              <TeamRoomHero
                teamName={team.team.name}
                memberNames={members.map((m) => m.name || m.agent_id)}
                leadName={leadName}
                accent={accent}
              />
            ) : (
              /* Not redundant with TeamTranscript's own `space-y-5`: that one
                 spaces MESSAGES, this one spaces the transcript from the typing
                 indicators and the scroll anchor below it. Collapsing them into
                 a fragment closes that gap. */
              <div className="space-y-5">
                <TeamTranscript
                  messages={messages}
                  userLabel={userLabel}
                  leadAgentId={leadAgentId ?? ''}
                  memberNames={memberNameMap}
                  renderSystem={(m) => <TeamSystemLine key={m.message_id} message={m} />}
                  renderHeader={(m) =>
                    !m.is_user && m.event_id ? (
                      <TeamMessageProcess agentId={m.from_agent} eventId={m.event_id} />
                    ) : null
                  }
                  renderFooter={(m) => (
                    <TeamMessageFooter
                      message={m}
                      turnArtifacts={m.event_id ? (wsTurns[m.event_id] ?? []) : []}
                      artifacts={wsArtifacts}
                      onOpenArtifact={(id) => { setWsSelected(id); setDrawerTab('artifacts'); }}
                    />
                  )}
                />

                {/* A sign of life for anyone who is NOT idle — the transcript
                    says who is up and nothing more. Everything measurable about
                    the run (elapsed, phases, tools) still belongs to the roster;
                    a finished turn still leaves the flow clean.

                    Was `status === 'running'`, which is a state a member only
                    reaches after the poll interval, a worker slot and Step 0.
                    `queued` is true within one 3s poll of the message landing,
                    so widening the filter is what closes the window where the
                    roster knew somebody was up and the conversation looked
                    dead — the "dead room" the PRD is named after. */}
                {activity
                  .filter((a) => a.status !== 'idle')
                  .map((a) => (
                    <LivenessIndicator
                      key={`liveness-${a.agent_id}`}
                      name={nameOf(a.agent_id)}
                      status={a.status as 'running' | 'queued' | 'stalled'}
                      detail={livenessDetail(t, a, now)}
                      highlighted={rosterExpandedId === a.agent_id}
                      onClick={() => {
                        toggleRoster(a.agent_id);
                        setDrawerTab('members');
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
            {composerError && (
              <div
                data-testid="composer-error"
                role="alert"
                className="mb-2 flex items-start gap-2 rounded-md border border-[var(--color-red-500)]/40 bg-[var(--color-red-500)]/10 px-2.5 py-1.5 text-xs text-[var(--nm-ink)]"
              >
                <span className="flex-1">{composerError}</span>
                <button
                  type="button"
                  onClick={() => setComposerError(null)}
                  className="p-0.5 rounded hover:bg-[var(--bg-secondary)]"
                  title={t('common.close')}
                  aria-label={t('common.close')}
                >
                  <X className="w-3 h-3 text-[var(--text-tertiary)]" />
                </button>
              </div>
            )}
            {transcriptionNotice && (
              <div className="mb-2 flex items-start gap-2 rounded-[var(--radius-md)] border border-[var(--rule)] bg-[var(--bg-tertiary)]/40 px-2.5 py-1.5 text-xs text-[var(--text-secondary)]">
                <Mic className="w-3.5 h-3.5 shrink-0 mt-0.5 text-[var(--text-tertiary)]" />
                <span className="flex-1">{transcriptionNotice}</span>
                <button
                  type="button"
                  onClick={() => setTranscriptionNotice(null)}
                  className="p-0.5 rounded hover:bg-[var(--nm-paper-warm)]"
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
                    className="relative flex items-center gap-2 rounded-[var(--radius-md)] border border-[var(--rule)] bg-[var(--bg-tertiary)]/60 pr-7 pl-1.5 py-1 max-w-[300px]"
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
                      className="absolute right-1 top-1 p-0.5 rounded hover:bg-[var(--nm-paper-warm)]"
                      title={t('chat.team.removeAttachment')}
                    >
                      <X className="w-3 h-3 text-[var(--text-tertiary)]" />
                    </button>
                  </div>
                ))}
                {uploading && (
                  <div className="flex items-center gap-1.5 px-2 py-1 rounded-[var(--radius-md)] border border-dashed border-[var(--rule)] text-[10px] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)] uppercase tracking-[0.1em]">
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
                    // Enter is how an IME candidate is ACCEPTED. Some IMEs fire
                    // compositionend before that final keydown, so the flag
                    // alone is not enough — hence the short grace window, the
                    // same pair the private chat's Composer settled on.
                    const composing =
                      e.nativeEvent.isComposing || isComposingRef.current;
                    if (composing || Date.now() - compositionEndTimeRef.current < 100) return;
                    e.preventDefault();
                    handleSend();
                  }
                }}
                onCompositionStart={() => {
                  isComposingRef.current = true;
                }}
                onCompositionUpdate={() => {
                  isComposingRef.current = true;
                }}
                onCompositionEnd={() => {
                  compositionEndTimeRef.current = Date.now();
                  setTimeout(() => {
                    isComposingRef.current = false;
                  }, 0);
                }}
                rows={1}
                placeholder={t('chat.team.placeholder')}
                className="nx-composer-input block min-h-[52px] max-h-[160px] py-[14px] pr-12 leading-[24px] resize-none bg-[color:var(--nm-card)] hover:border-[color:var(--nm-hairline)] focus:border-[color:var(--nm-hairline)]"
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

        {/* The room's right side — the SAME drawer as single chat (shared
            pin/width preferences, title-dropdown switching), hosting the
            team's panels: members / artifacts / shared files. Pinned it is
            a static resizable column; unpinned an in-flow transient on
            desktop and an overlay on phones — identical semantics to the
            single-chat panels. */}
        {drawerPinned && drawerTab && !isMobile && (
          <ResizableDivider
            onResize={handleDrawerResize}
            onResizeEnd={handleDrawerResizeEnd}
            label={t('layout.resizableDivider.drawerAriaLabel')}
            title={t('layout.resizableDivider.drawerTitle')}
          />
        )}
        <BookmarkDrawer
          open={drawerTab !== null}
          pinned={drawerPinned && !isMobile}
          onPinnedChange={setDrawerPinned}
          onClose={() => setDrawerTab(null)}
          title={drawerTab ? t(teamTabLabelKey(drawerTab)) : ''}
          // Title switcher (Owner-required): every team panel, incl. files and
          // manage, is reachable from the drawer itself.
          activeTab={drawerTab}
          onSelectTab={(id) => setDrawerTab(id)}
          switcherCategories={teamDrawerCategories({ members: members.length, artifacts: wsArtifacts.length, files: wsFiles.length })}
          edgeReservePx={0}
          pinnedWidth={drawerWidth}
          inset={!isMobile}
          insetWidth={
            drawerTab === 'artifacts'
              ? 'min(max(440px, 50vw), max(320px, calc(100vw - 672px)))'
              : 'min(440px, max(320px, calc(100vw - 672px)))'
          }
          columnRef={drawerColRef}
        >
          {drawerTab === 'members' && (
            <TeamRosterPanel
              teamId={teamId}
              members={members}
              activity={activity}
              leadAgentId={leadAgentId}
              now={now}
              expandedId={rosterExpandedId}
              onToggle={toggleRoster}
              accent={accent}
              onOpenSettings={() => navigate(`/app/teams/${teamId}`)}
              onSetLead={handleSetLead}
              className="flex h-full w-full"
            />
          )}
          {drawerTab === 'manage' && (
            <TeamManagePanel
              teamId={teamId}
              teamName={team.team.name}
              team={team.team}
              members={members}
              allAgents={agents}
              leadAgentId={leadAgentId}
              onSetLead={handleSetLead}
              bulletin={bulletin}
              bulletinLoading={bulletinLoading}
              bulletinError={bulletinError}
              memberNames={memberNameMap}
              onBulletinAdd={(content, tier) =>
                bulletinAction(() => api.createTeamBulletinEntry(teamId, { content, tier }))
              }
              onBulletinEdit={(entryId, content) =>
                bulletinAction(() => api.updateTeamBulletinEntry(teamId, entryId, content))
              }
              onBulletinDelete={(entryId) =>
                bulletinAction(() => api.deleteTeamBulletinEntry(teamId, entryId))
              }
              onBulletinClearTier={(tier) =>
                bulletinAction(() => api.clearTeamBulletinTier(teamId, tier))
              }
              onCleared={handleCleared}
              className="h-full w-full"
            />
          )}
          {(drawerTab === 'artifacts' || drawerTab === 'files') && (
            <TeamWorkspacePanel
              tab={drawerTab}
              artifacts={wsArtifacts}
              files={wsFiles}
              loading={wsLoading}
              error={wsError}
              selectedId={wsSelected}
              onSelect={setWsSelected}
            />
          )}
        </BookmarkDrawer>
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
    </div>
  );
}
