/**
 * @file_name: teamTabs.ts
 * @author: NarraNexus
 * @date: 2026-08-19
 * @description: The team room's drawer panels — members, artifacts, shared
 * files, team management — as ids, label keys, and the switcher registry
 * for the shared BookmarkDrawer's title dropdown.
 *
 * The team room's right side IS the single-chat right side: same drawer,
 * same pin/width preferences, same title-dropdown switching (Owner-required,
 * reinstated 2026-09-11). Only the panel set differs, and this file is that
 * difference. Each panel is ALSO opened by its own toggle in the member bar;
 * both entry points coexist. `Record<TeamTabId, …>` keeps the label map
 * exhaustive — adding a panel that forgets its label is a compile error.
 */

import { Users2, FolderOpen, Settings2 } from 'lucide-react';
import { ArtifactsGlyph, type DrawerSwitcherCategory } from '@/components/bookmarks';

export type TeamTabId = 'members' | 'artifacts' | 'files' | 'manage';

const TAB_LABEL_KEYS: Record<TeamTabId, string> = {
  members: 'chat.team.roster.title',
  artifacts: 'chat.team.workspace.tabArtifacts',
  files: 'chat.team.workspace.tabFiles',
  manage: 'chat.team.manage.title',
};

export interface TeamTabCounts {
  members: number;
  artifacts: number;
  files: number;
}

/** The team drawer's switcher registry. Live counts ride on the entries —
 *  an entry that never advertises its contents is closed exactly when it
 *  mattered (shared files especially). */
export function teamDrawerCategories(
  counts: TeamTabCounts,
): ReadonlyArray<DrawerSwitcherCategory<TeamTabId>> {
  return [
    {
      label: 'Team room',
      labelKey: 'chat.team.drawerCategory',
      tabs: [
        { id: 'members', labelKey: TAB_LABEL_KEYS.members, icon: Users2, count: counts.members },
        { id: 'artifacts', labelKey: TAB_LABEL_KEYS.artifacts, icon: ArtifactsGlyph, count: counts.artifacts },
        { id: 'files', labelKey: TAB_LABEL_KEYS.files, icon: FolderOpen, count: counts.files },
        // Management last: bulletin, lead, patrol, members, clear, delete.
        { id: 'manage', labelKey: TAB_LABEL_KEYS.manage, icon: Settings2 },
      ],
    },
  ];
}

export function teamTabLabelKey(id: TeamTabId): string {
  return TAB_LABEL_KEYS[id] ?? 'chat.team.roster.title';
}
