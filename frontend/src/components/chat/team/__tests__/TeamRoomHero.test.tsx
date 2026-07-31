/**
 * @file_name: TeamRoomHero.test.tsx
 * @description: What an empty team room must say for itself.
 *
 * The hero replaces a permanent grey banner, so the two things it owes the user
 * are pinned here: the room's addressing rules stay stated (with WHO answers an
 * un-addressed message named), and the room shows who is actually in it —
 * capped, because a 20-agent team must not turn the hero into an avatar wall.
 */
import { describe, expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TeamRoomHero } from '../TeamRoomHero';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, v?: Record<string, unknown>) =>
      v ? `${k}(${Object.values(v).join(',')})` : k,
  }),
}));

const ACCENT = 'var(--color-silicon)';

describe('TeamRoomHero', () => {
  test('renders rule cards with titles and the default responder', () => {
    render(
      <TeamRoomHero
        teamName="Desk"
        memberNames={['Red', 'Bruno']}
        leadName="Red"
        accent={ACCENT}
      />,
    );

    expect(screen.getByText('chat.team.guide.plainTitle')).toBeTruthy();
    expect(screen.getByText('chat.team.guide.mentionTitle')).toBeTruthy();
    expect(screen.getByText('chat.team.guide.broadcastTitle')).toBeTruthy();

    // Who answers when you address nobody is the single most useful fact.
    expect(screen.getByText('chat.team.guide.plainWithLead(Red)')).toBeTruthy();
    expect(screen.getByText('Desk')).toBeTruthy();
  });

  test('renders member avatars capped at five with an overflow chip', () => {
    const { container } = render(
      <TeamRoomHero
        teamName="Desk"
        memberNames={['Ana', 'Bruno', 'Cleo', 'Dara', 'Eli', 'Fay', 'Gus']}
        leadName="Ana"
        accent={ACCENT}
      />,
    );

    expect(container.querySelectorAll('[data-nm="ring-avatar"]')).toHaveLength(5);
    expect(screen.getByText('+2')).toBeTruthy();
  });

  test('zero members shows the no-agents line but keeps the cards', () => {
    const { container } = render(
      <TeamRoomHero teamName="Desk" memberNames={[]} leadName={null} accent={ACCENT} />,
    );

    expect(container.querySelectorAll('[data-nm="ring-avatar"]')).toHaveLength(0);
    expect(screen.getByText('chat.team.noAgents')).toBeTruthy();
    // The rules are what the room is FOR — they survive an empty roster.
    expect(screen.getByText('chat.team.guide.plainTitle')).toBeTruthy();
    expect(screen.getByText('chat.team.guide.plain')).toBeTruthy();
  });
});
