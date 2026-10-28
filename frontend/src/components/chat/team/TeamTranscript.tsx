/**
 * @file_name: TeamTranscript.tsx
 * @author: NarraNexus
 * @date: 2026-08-12
 * @description: The team room's message stream.
 *
 * Exists as its own component because "when did this happen" is a property of
 * the SEQUENCE, not of any one message — a bubble cannot know whether it is the
 * first of a new day. That is also the single thing this adds over the loop it
 * replaces.
 *
 * A team room is an ASYNC space: the user hands it work and comes back later.
 * Without separators, Monday's message sits flush against Thursday's and reads
 * as one conversation. The private chat has had them for a long time; the room
 * is where they matter more, not less.
 *
 * Days are cut in LOCAL time. Two instants can share a UTC date and fall on
 * different days for the reader, and the separator's only job is to agree with
 * the clock on their wall.
 *
 * System lines (a stop notice, a bulletin change, a patrol sweep) are the room
 * speaking, not a member: they are handed back to the caller to render as
 * centred notices rather than given an identity and a bubble. They still open a
 * day, or a day whose only event was one of them would look like part of the
 * previous one.
 */

import { Fragment } from 'react';
import { useTranslation } from 'react-i18next';

import { TeamMessageBubble } from './TeamMessageBubble';
import { activeLocale } from '@/lib/utils';
import type { TeamChatMessage } from '@/types/teams';

/**
 * Is this line the platform narrating itself?
 *
 * The SERVER decides, and sends `is_platform` with each message. This used to be
 * a `Set` mirroring `message_bus/system_messages.PLATFORM_MSG_TYPES`, with a
 * comment claiming a test guarded it — the test was a third hand-written copy,
 * and it was already two types short. The wire carries strings, so a type the
 * server sends and the client does not know renders as a MEMBER speaking: an
 * identity colour, an avatar, and a `team_<id>` marker where a name should be.
 * That list grew from five entries to seven in a single change, so "remember to
 * update the other copy" was never going to hold.
 *
 * `msg_type` still chooses the wording — that is rendering, and it belongs to
 * whoever renders. What moved is only the yes/no.
 */
function isPlatformLine(m: TeamChatMessage): boolean {
  return m.is_platform === true;
}

export interface TeamTranscriptProps {
  messages: TeamChatMessage[];
  userLabel: string;
  leadAgentId?: string;
  memberNames: Record<string, string>;
  /** Render a platform line. Returning null drops it. */
  renderSystem?: (m: TeamChatMessage) => React.ReactNode;
  /** Rendered at the top of an agent bubble (the process disclosure). */
  renderHeader?: (m: TeamChatMessage) => React.ReactNode;
  /** Rendered under a bubble's content (artifact chips, timestamp). */
  renderFooter?: (m: TeamChatMessage) => React.ReactNode;
}

/**
 * Local calendar day, or null when the timestamp cannot be read.
 *
 * A bad row costs its separator, never its content — the message still renders.
 */
function dayKey(iso: string): string | null {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return new Date(t).toDateString();
}

/**
 * The separator's label. `activeLocale()` rather than `i18n.language`: it is the
 * one guard in the product against a malformed language tag, and `Intl` throws
 * on those DURING RENDER — a blank timestamp elsewhere, but the whole transcript
 * to an error boundary here. It also resolves `zh` to `zh-CN` the same way the
 * message timestamps do, so the two cannot disagree.
 */
function dayLabel(key: string): string {
  const d = new Date(key);
  const today = new Date().toDateString();
  if (key === today) return '';
  return d.toLocaleDateString(activeLocale(), {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

export function TeamTranscript({
  messages,
  userLabel,
  leadAgentId = '',
  memberNames,
  renderSystem,
  renderHeader,
  renderFooter,
}: TeamTranscriptProps) {
  const { t } = useTranslation();
  let lastDay: string | null = null;

  return (
    <div className="space-y-5">
      {messages.map((m) => {
        const key = dayKey(m.created_at);
        const opensDay = key !== null && key !== lastDay;
        if (opensDay) lastDay = key;

        const isSystem = isPlatformLine(m);

        return (
          <Fragment key={m.message_id}>
            {opensDay && (
              <div
                data-testid={`day-sep-${key}`}
                className="flex items-center gap-3 py-1 text-[10px] font-mono text-[var(--text-tertiary)]"
              >
                <span className="h-px flex-1 bg-[var(--border-subtle)]" />
                <span>{dayLabel(key as string) || t('chat.team.today')}</span>
                <span className="h-px flex-1 bg-[var(--border-subtle)]" />
              </div>
            )}
            {isSystem ? (
              renderSystem?.(m) ?? null
            ) : (
              <TeamMessageBubble
                message={m}
                userLabel={userLabel}
                leadAgentId={leadAgentId}
                memberNames={memberNames}
                header={renderHeader?.(m)}
                footer={renderFooter?.(m)}
              />
            )}
          </Fragment>
        );
      })}
    </div>
  );
}

export default TeamTranscript;
