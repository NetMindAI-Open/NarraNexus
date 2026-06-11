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
        note: 'Create an agent',
        detail: 'One click. Then shape who it is by simply chatting with it.',
        rail: 'left',
        priority: 1,
      },
      {
        helpId: 'sidebar.manage-agents',
        note: 'Manage agents',
        detail: 'Batch view — rename, delete, organize all agents in one page.',
        rail: 'left',
        priority: 2,
      },
      {
        helpId: 'bookmarks.awareness',
        note: 'Awareness — who your agent is',
        detail: 'Edit it by hand here, or just tell the agent in chat; it keeps its own awareness updated.',
        rail: 'right',
        priority: 3,
      },
      {
        helpId: 'bookmarks.workspace',
        note: 'Workspace',
        detail: 'Upload files your agent can read and use.',
        rail: 'right',
        priority: 4,
      },
      {
        helpId: 'bookmarks.channels',
        note: 'Channels',
        detail: 'Wire the agent into Lark, Slack or Telegram.',
        rail: 'right',
        priority: 5,
      },
      {
        helpId: 'bookmarks.skills',
        note: 'Skills',
        detail: 'Install abilities from GitHub or a zip; the agent studies them.',
        rail: 'right',
        priority: 6,
      },
      {
        helpId: 'bookmarks.mcp',
        note: 'MCP servers',
        detail: 'Plug in external tool servers your agent can call.',
        rail: 'right',
        priority: 7,
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
        helpId: 'sidebar.agents-menu',
        note: 'The three-dots menu',
        detail: 'Create & manage teams. Export agents or whole teams as a .nxbundle; import bundles or marketplace templates.',
        rail: 'left',
        priority: 3,
      },
      {
        helpId: 'bookmarks.social',
        note: 'Social network',
        detail: 'Who your agent knows — contacts accumulate as it works with people and other agents.',
        rail: 'right',
        priority: 4,
      },
    ],
  },
];
