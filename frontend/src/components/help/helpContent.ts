/**
 * @file_name: helpContent.ts
 * @author:
 * @date: 2026-06-11
 * @description: Declarative annotation manifests for the help overlay,
 * organized as PAGES (Owner 2026-06-11: one overlay page per topic —
 * Agent Setup / Interacting / Teams & Bundles — switched by tabs under
 * the centered "got it" control).
 *
 * Each annotation references a `data-help-id` anchor in the live DOM.
 * The overlay measures anchors at open time and silently skips any that
 * are missing or invisible — manifests never break when layout evolves.
 *
 * `rail` decides where the note SITS (notes are stacked per rail and
 * never overlap); the arrow always travels from the note to its anchor.
 *
 * Density discipline (spec §12.5): ≤ 8 annotations per page.
 */

export type AnnotationRail = 'left' | 'right' | 'top';

export interface HelpAnnotation {
  /** Matches a `data-help-id` attribute in the DOM. */
  helpId: string;
  /** Handwritten headline (short). */
  note: string;
  /** Optional smaller second line — the "explain a bit more" layer. */
  detail?: string;
  /** Which note rail this annotation's text sits in. */
  rail: AnnotationRail;
  /** Lower = drawn earlier in the stagger animation + higher in rail. */
  priority: number;
}

export interface HelpPage {
  id: string;
  /** Page-tab label shown in the overlay footer. */
  label: string;
  annotations: HelpAnnotation[];
}

export const CHAT_VIEW_PAGES: HelpPage[] = [
  {
    id: 'setup',
    label: 'Agent Setup',
    annotations: [
      {
        helpId: 'sidebar.create-agent',
        note: 'New — create or import',
        detail: 'Create an agent or team, or import one — then shape it by simply chatting with it.',
        rail: 'left',
        priority: 1,
      },
      {
        helpId: 'sidebar.manage-agents',
        note: 'Dashboard',
        detail: 'Every agent in one page — status, jobs, batch rename / delete / organize.',
        rail: 'left',
        priority: 2,
      },
      {
        // v4 folded the old bookmark-strip panels into the header ⋯ menu, so
        // the five per-panel annotations merged into one on its (always
        // rendered) trigger — menu items have no anchor while closed.
        helpId: 'chat.detail-menu',
        note: 'Agent panels',
        detail: 'Awareness (who your agent is), workspace files, channels, skills and MCP servers — every agent panel lives in this menu.',
        rail: 'right',
        priority: 3,
      },
    ],
  },
  {
    id: 'interact',
    label: 'Interacting',
    annotations: [
      {
        helpId: 'chat.composer',
        note: 'Talk to your agent here',
        detail: 'Drag files in to attach them; voice input supported.',
        rail: 'top',
        priority: 1,
      },
      {
        helpId: 'chat.messages',
        note: 'The conversation',
        detail: 'Thinking, tool calls and replies stream here in real time.',
        rail: 'left',
        priority: 2,
      },
      {
        helpId: 'layout.artifacts',
        note: 'Artifacts',
        detail: 'Reports, charts and pages the agent produces open beside the chat.',
        rail: 'right',
        priority: 3,
      },
      {
        helpId: 'bookmarks.jobs',
        note: 'Jobs',
        detail: 'Scheduled work. Just ask in chat — "every morning send me a brief" — and the agent creates one.',
        rail: 'right',
        priority: 4,
      },
      {
        helpId: 'bookmarks.inbox',
        note: 'Inbox',
        detail: 'Messages arriving from channels and from other agents.',
        rail: 'right',
        priority: 5,
      },
      {
        helpId: 'chat.cost',
        note: 'Cost',
        detail: 'What this conversation has cost so far.',
        rail: 'top',
        priority: 6,
      },
    ],
  },
  {
    id: 'teams',
    label: 'Teams & Bundles',
    annotations: [
      {
        helpId: 'sidebar.agent-list',
        note: 'Agents, grouped by team',
        detail: 'Teams are the sections of this list.',
        rail: 'left',
        priority: 1,
      },
      {
        helpId: 'sidebar.team-section',
        note: 'A team section',
        detail: 'Click the header to fold a team; hover and press the arrow to open its page.',
        rail: 'left',
        priority: 2,
      },
      {
        helpId: 'sidebar.export',
        note: 'Export bundles',
        detail: 'Package agents or whole teams as a .nxbundle to back up or share; import them back from New.',
        rail: 'left',
        priority: 3,
      },
      {
        // Anchored on the ⋯ menu trigger: the Social panel now opens from it.
        helpId: 'chat.detail-menu',
        note: 'Social network',
        detail: 'Who your agent knows — open Social from this menu; contacts accumulate as it works with people and other agents.',
        rail: 'right',
        priority: 4,
      },
    ],
  },
];
