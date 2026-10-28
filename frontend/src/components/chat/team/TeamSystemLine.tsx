/**
 * @file_name: TeamSystemLine.tsx
 * @author: NarraNexus
 * @date: 2026-08-12
 * @description: A line the ROOM wrote, not a member.
 *
 * Several kinds: a run stopped by the owner, a bulletin change, a patrol sweep,
 * a capped cascade, a roster change, a turn that reached nobody and a reply the
 * room never received. All of them are the platform narrating itself, so none
 * gets an avatar, an identity colour or a bubble — dressing a platform event as
 * a member's message attributes it to whoever happened to trigger it, and in
 * patrol's case to a `team_<id>` marker that resolves to no member at all.
 *
 * Lifted out of TeamChatPanel unchanged. The markup and the reasoning are the
 * ones that were already there; only the location moved, so that the transcript
 * can decide WHERE a system line goes without also owning what it looks like.
 */

import { useTranslation } from 'react-i18next';

import { Markdown } from '@/components/ui';
import type { TeamChatMessage } from '@/types/teams';

export function TeamSystemLine({ message: m }: { message: TeamChatMessage }) {
  const { t } = useTranslation();

  if (m.msg_type === 'system_bulletin') {
    return (
      <div data-testid={`bulletin-notice-${m.message_id}`} className="flex justify-center py-1">
        <span
          className="rounded-full border border-[var(--border-subtle)] px-2.5 py-0.5 text-[10px] font-mono"
          style={{ color: 'var(--nm-ink50)' }}
        >
          {t(
            m.content?.includes('cleared')
              ? 'chat.team.bulletin.clearedNotice'
              : 'chat.team.bulletin.updatedNotice',
          )}
        </span>
      </div>
    );
  }

  if (m.msg_type === 'system_stop') {
    return (
      <div data-testid={`stop-notice-${m.message_id}`} className="flex justify-center py-1">
        <span
          className="rounded-full border border-[var(--border-subtle)] px-2.5 py-0.5 text-[10px] font-mono"
          style={{ color: 'var(--nm-ink50)' }}
        >
          {t('chat.team.stoppedNotice', { name: m.author_name })}
        </span>
      </div>
    );
  }

  // A turn that reached nobody, and a reply the room never received. Both are
  // room-level lines for the same reason as the stop notice — the platform is
  // the one speaking. The failure keeps its reason in `title`: the chip stays
  // quiet in the transcript, and the detail is one hover away for whoever is
  // debugging.
  if (m.msg_type === 'system_undelivered' || m.msg_type === 'system_delivery_failed') {
    const failed = m.msg_type === 'system_delivery_failed';
    return (
      <div
        data-testid={`${failed ? 'delivery-failed' : 'undelivered'}-notice-${m.message_id}`}
        className="flex justify-center py-1"
      >
        <span
          title={m.content}
          className="rounded-full border px-2.5 py-0.5 text-[10px] font-mono"
          style={{
            // A failed post is OUR fault and actionable, so it carries warning
            // weight; a silent turn is merely information and stays as quiet as
            // the other platform lines.
            color: failed ? 'var(--color-warning)' : 'var(--nm-ink50)',
            borderColor: failed ? 'var(--color-warning)' : 'var(--border-subtle)',
          }}
        >
          {t(
            failed ? 'chat.team.deliveryFailedNotice' : 'chat.team.undeliveredNotice',
            { name: m.author_name },
          )}
        </span>
      </div>
    );
  }

  // The cap fired: the user asked for a teammate and the platform declined.
  // Shown as its own line rather than folded into the reply, because it is not
  // the agent's statement — and it names who to @ manually.
  if (m.msg_type === 'system_cascade') {
    return (
      <div data-testid={`cascade-notice-${m.message_id}`} className="flex justify-center py-1">
        <span
          className="rounded-full border border-[var(--border-subtle)] px-2.5 py-0.5 text-[10px] font-mono"
          style={{ color: 'var(--nm-ink50)' }}
        >
          {m.content}
        </span>
      </div>
    );
  }

  // Membership or lead changed. Rendered as a line in the flow, at the point it
  // happened, so the transcript above it is not read as the current roster's.
  if (m.msg_type === 'system_roster') {
    return (
      <div data-testid={`roster-notice-${m.message_id}`} className="flex justify-center py-1">
        <span
          className="rounded-full border border-[var(--border-subtle)] px-2.5 py-0.5 text-[10px] font-mono"
          style={{ color: 'var(--nm-ink50)' }}
        >
          {m.content}
        </span>
      </div>
    );
  }

  // Patrol: the platform taking stock. Shown, because the chase it carries is
  // real work the room should see, but never as a member speaking.
  return (
    <div data-testid={`patrol-notice-${m.message_id}`} className="flex justify-center py-1">
      <div className="max-w-[85%] rounded-[var(--radius-lg)] border border-dashed border-[var(--border-subtle)] px-3 py-2">
        <div className="mb-1 text-[10px] font-mono uppercase" style={{ color: 'var(--nm-ink50)' }}>
          {t('chat.team.patrolNotice')}
        </div>
        <div className="text-sm" style={{ color: 'var(--nm-ink70)' }}>
          <Markdown content={(m.content || '').trim()} />
        </div>
      </div>
    </div>
  );
}

export default TeamSystemLine;
