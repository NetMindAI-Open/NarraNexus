/**
 * @file_name: TeamRoomHero.tsx
 * @author:
 * @date: 2026-07-30
 * @description: The empty team room's opening screen, plus the addressing-rule
 * cards it shares with the member bar's help popover.
 *
 * A team room has addressing rules a 1:1 chat does not: an un-addressed message
 * is not dropped — it goes to the room's default responder — @ names one
 * teammate, and @all runs every member. Those rules used to be stated by a
 * permanent grey banner pinned above the transcript, which read as debt: it
 * occupied a strip of the room forever, for a fact the user needs exactly twice
 * (the first visit, and the day they forget).
 *
 * So the rules move to the two moments they are wanted. `TeamRoomHero` fills
 * the room while it is empty — the only time there is nothing else to look at —
 * with who is in the room, what it is called, and the rules. `GuideRuleCards`
 * is the same block on its own, so the member bar's `?` popover can raise it
 * once the transcript has taken over the space.
 */

import { useTranslation } from 'react-i18next';
import { AtSign, Megaphone, MessageSquare } from 'lucide-react';
import { RingAvatar } from '@/components/nm';

/** How many member avatars the hero shows before collapsing the rest to "+N". */
const MAX_AVATARS = 5;

/**
 * The three addressing rules as cards. Shared by the hero and the member bar's
 * help popover, so the room only ever states its rules one way.
 */
export function GuideRuleCards({
  leadName,
  accent,
}: {
  /** Display name of the default responder; null when the team has no members. */
  leadName: string | null;
  accent: string;
}) {
  const { t } = useTranslation();

  const rules: Array<{ icon: typeof AtSign; title: string; body: string }> = [
    {
      icon: MessageSquare,
      title: t('chat.team.guide.plainTitle'),
      // Naming WHO answers beats saying "the team lead" — it is the one fact a
      // room you just opened cannot show you any other way.
      body: leadName
        ? t('chat.team.guide.plainWithLead', { name: leadName })
        : t('chat.team.guide.plain'),
    },
    {
      icon: AtSign,
      title: t('chat.team.guide.mentionTitle'),
      body: t('chat.team.guide.mention'),
    },
    {
      icon: Megaphone,
      title: t('chat.team.guide.broadcastTitle'),
      body: t('chat.team.guide.broadcast'),
    },
  ];

  return (
    <div className="space-y-2">
      {rules.map(({ icon: Icon, title, body }) => (
        <div
          key={title}
          className="rounded-[var(--radius-md)] border border-[var(--rule)] bg-[var(--bg-secondary)]/40 px-3 py-2.5 flex items-start gap-2.5"
        >
          <span
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--radius-sm)]"
            style={{ background: `color-mix(in srgb, ${accent} 12%, transparent)` }}
          >
            <Icon className="h-3.5 w-3.5" style={{ color: accent }} />
          </span>
          <div className="min-w-0">
            <div className="text-[13px] font-medium text-[var(--nm-ink)]">{title}</div>
            <div className="text-[11px] leading-relaxed text-[var(--text-secondary)]">{body}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * What an empty room shows instead of a transcript: the members who are in it,
 * the room's name, and how to address it.
 */
export function TeamRoomHero({
  teamName,
  memberNames,
  leadName,
  accent,
}: {
  teamName: string;
  memberNames: string[];
  /** Display name of the default responder; null when the team has no members. */
  leadName: string | null;
  accent: string;
}) {
  const { t } = useTranslation();
  const shown = memberNames.slice(0, MAX_AVATARS);
  const overflow = memberNames.length - shown.length;

  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 px-6 text-center">
      {memberNames.length === 0 ? (
        <div className="text-xs text-[var(--text-tertiary)]">{t('chat.team.noAgents')}</div>
      ) : (
        <div className="flex -space-x-2">
          {shown.map((name) => (
            <RingAvatar
              key={name}
              species="silicon"
              label={name.slice(0, 2)}
              size="sm"
              title={name}
              className="ring-2 ring-[var(--nm-paper)]"
            />
          ))}
          {overflow > 0 && (
            <span className="flex h-8 w-8 items-center justify-center rounded-full border-2 border-[var(--nm-hairline)] bg-[var(--nm-paper-warm)] text-[11px] font-semibold text-[var(--text-secondary)] ring-2 ring-[var(--nm-paper)]">
              +{overflow}
            </span>
          )}
        </div>
      )}

      <div className="text-sm font-medium text-[var(--nm-ink)]">{teamName}</div>

      {/* A hand-off chain that stops is the relay cap, not a bug. */}
      <p className="max-w-[300px] text-[11px] text-[var(--text-tertiary)]">
        {t('chat.team.guide.relay')}
      </p>

      <div className="w-full max-w-sm text-left">
        <GuideRuleCards leadName={leadName} accent={accent} />
      </div>
    </div>
  );
}
