/**
 * @file_name: index.ts
 * @author:
 * @date: 2026-07-28
 * @description: Barrel for the team group-chat surface.
 *
 * `TeamChatPanel` is the only member the rest of the app mounts; the roster
 * and the empty-room hero are its parts and are exported for tests, not for
 * other screens to compose with.
 */

export { TeamChatPanel } from './TeamChatPanel';
export { GuideRuleCards, TeamRoomHero } from './TeamRoomHero';
export { TeamRosterPanel } from './TeamRosterPanel';
export { TeamMemberPanel } from './TeamMemberPanel';
