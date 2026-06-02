/**
 * Mock fixtures — hand-authored realistic data so every panel has
 * something to render. All shapes follow src/types/api.ts.
 *
 * Design intent: mix states (running / completed / failed / pending),
 * mix scales (short name vs long name), and give enough variety to
 * exercise long-content / truncation / empty-state branches.
 */

import type {
  Job,
  InboxRoom,
  SocialNetworkEntity,
  ChatHistoryEvent,
  ChatHistoryNarrative,
  InstanceInfo,
  SimpleChatMessage,
  MCPInfo,
  RAGFileInfo,
  CostSummary,
  EmbeddingStatusData,
  AgentInfo,
} from '@/types/api';
import type { SkillInfo, SkillListResponse } from '@/types/skills';
import type { AgentStatus, OwnedAgentStatus, PublicAgentStatus } from '@/types/api';

const NOW = Date.now();
const ONE_MIN = 60_000;
const ONE_HOUR = 60 * ONE_MIN;
const ONE_DAY = 24 * ONE_HOUR;
const iso = (offset: number) => new Date(NOW + offset).toISOString();
const localDateTime = (offset: number) => {
  const d = new Date(NOW + offset);
  // naive local ISO like "2026-04-22T14:35:00"
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
};

/* ─────────────────────────────── Agents ─────────────── */

export const MOCK_USER_ID = 'demo.user';
export const MOCK_AGENT_ID = 'atlas';
export const MOCK_SECOND_AGENT_ID = 'scribe';
export const MOCK_THIRD_AGENT_ID = 'sentry';

export const mockAgents: AgentInfo[] = [
  {
    agent_id: MOCK_AGENT_ID,
    name: 'Atlas',
    description: 'Research agent — reads papers, drafts summaries, schedules follow-ups.',
    status: 'active',
    created_at: iso(-14 * ONE_DAY),
    is_public: false,
    created_by: MOCK_USER_ID,
    bootstrap_active: false,
  },
  {
    agent_id: MOCK_SECOND_AGENT_ID,
    name: 'Scribe',
    description: 'Note-taking & meeting summariser for the product team.',
    status: 'idle',
    created_at: iso(-7 * ONE_DAY),
    is_public: true,
    created_by: MOCK_USER_ID,
    bootstrap_active: false,
  },
  {
    agent_id: MOCK_THIRD_AGENT_ID,
    name: 'Sentry',
    description: 'Ops monitor — watches CI, alerts on job failures.',
    status: 'error',
    created_at: iso(-30 * ONE_DAY),
    is_public: false,
    created_by: 'ops.team',
    bootstrap_active: false,
  },
];

/* ─────────────────────────────── Jobs ───────────────── */

export const mockJobs: Job[] = [
  {
    job_id: 'job_a1b2c3d4',
    agent_id: MOCK_AGENT_ID,
    user_id: MOCK_USER_ID,
    job_type: 'scheduled',
    title: 'Weekly research digest',
    description: 'Compile a summary of ArXiv papers tagged with "alignment" and post to the shared Notion page.',
    status: 'running',
    trigger_config: { trigger_type: 'cron', cron_expression: '0 9 * * MON', timezone: 'Asia/Shanghai' },
    process: ['fetch_arxiv', 'rank_papers', 'summarise_top_10', 'publish_notion'],
    next_run_at: localDateTime(3 * ONE_DAY + 6 * ONE_HOUR),
    next_run_timezone: 'Asia/Shanghai',
    last_run_at: localDateTime(-4 * ONE_DAY),
    last_run_timezone: 'Asia/Shanghai',
    created_at: iso(-14 * ONE_DAY),
    updated_at: iso(-2 * ONE_MIN),
    related_entity_id: 'user_demo',
    depends_on: [],
  },
  {
    job_id: 'job_e5f6g7h8',
    agent_id: MOCK_AGENT_ID,
    user_id: MOCK_USER_ID,
    job_type: 'one_off',
    title: 'Draft reply to Prof. Chen',
    description: 'Summarise our latest findings and reply to the email thread.',
    status: 'pending',
    trigger_config: { trigger_type: 'manual' },
    next_run_at: localDateTime(20 * ONE_MIN),
    next_run_timezone: 'Asia/Shanghai',
    created_at: iso(-30 * ONE_MIN),
    updated_at: iso(-30 * ONE_MIN),
    depends_on: ['job_a1b2c3d4'],
  },
  {
    job_id: 'job_i9j0k1l2',
    agent_id: MOCK_AGENT_ID,
    user_id: MOCK_USER_ID,
    job_type: 'ongoing',
    title: 'Slack channel watcher',
    description: 'Watch #alerts and escalate P1 incidents.',
    status: 'active',
    trigger_config: { trigger_type: 'event', interval_seconds: 60 },
    next_run_at: localDateTime(1 * ONE_MIN),
    next_run_timezone: 'Asia/Shanghai',
    last_run_at: localDateTime(-45),
    last_run_timezone: 'Asia/Shanghai',
    created_at: iso(-60 * ONE_DAY),
    updated_at: iso(-45 * 1000),
  },
  {
    job_id: 'job_m3n4o5p6',
    agent_id: MOCK_AGENT_ID,
    user_id: MOCK_USER_ID,
    job_type: 'scheduled',
    title: 'Daily standup prep',
    description: 'Pull yesterday\'s commits and open tickets; post a bullet list to DMs.',
    status: 'completed',
    trigger_config: { trigger_type: 'cron', cron_expression: '45 8 * * 1-5', timezone: 'Asia/Shanghai' },
    last_run_at: localDateTime(-1 * ONE_HOUR),
    last_run_timezone: 'Asia/Shanghai',
    next_run_at: localDateTime(ONE_DAY - 1 * ONE_HOUR),
    next_run_timezone: 'Asia/Shanghai',
    created_at: iso(-21 * ONE_DAY),
    updated_at: iso(-1 * ONE_HOUR),
  },
  {
    job_id: 'job_q7r8s9t0',
    agent_id: MOCK_AGENT_ID,
    user_id: MOCK_USER_ID,
    job_type: 'one_off',
    title: 'Failed: import legacy transcripts',
    description: 'Batch-import Zoom transcripts from 2024-Q4.',
    status: 'failed',
    trigger_config: { trigger_type: 'manual' },
    last_run_at: localDateTime(-6 * ONE_HOUR),
    last_run_timezone: 'Asia/Shanghai',
    last_error: 'HTTP 413 — 7 files exceeded 512 MB upload limit. Retry with chunked upload.',
    created_at: iso(-1 * ONE_DAY),
    updated_at: iso(-6 * ONE_HOUR),
  },
  {
    job_id: 'job_u1v2w3x4',
    agent_id: MOCK_AGENT_ID,
    user_id: MOCK_USER_ID,
    job_type: 'scheduled',
    title: 'Paused: quarterly report draft',
    description: 'Will resume when Q1 data is ready.',
    status: 'paused',
    trigger_config: { trigger_type: 'cron', cron_expression: '0 10 1 */3 *', timezone: 'Asia/Shanghai' },
    created_at: iso(-45 * ONE_DAY),
    updated_at: iso(-3 * ONE_DAY),
  },
  {
    job_id: 'job_y5z6a7b8',
    agent_id: MOCK_AGENT_ID,
    user_id: MOCK_USER_ID,
    job_type: 'one_off',
    title: 'Blocked: awaiting GitHub OAuth scope',
    description: 'Cannot run until org grants repo:read scope to the app.',
    status: 'blocked',
    trigger_config: { trigger_type: 'manual' },
    created_at: iso(-2 * ONE_DAY),
    updated_at: iso(-2 * ONE_HOUR),
  },
  {
    job_id: 'job_c9d0e1f2',
    agent_id: MOCK_AGENT_ID,
    user_id: MOCK_USER_ID,
    job_type: 'one_off',
    title: 'Cancelled: duplicate run',
    description: 'Superseded by job_a1b2c3d4.',
    status: 'cancelled',
    trigger_config: { trigger_type: 'manual' },
    created_at: iso(-10 * ONE_DAY),
    updated_at: iso(-10 * ONE_DAY),
  },
];

/* ─────────────────────────────── Inbox rooms ─────────── */

export const mockInboxRooms: InboxRoom[] = [
  {
    room_id: 'room_eng_alerts',
    room_name: 'eng-alerts',
    members: [
      { agent_id: MOCK_AGENT_ID, agent_name: 'Atlas' },
      { agent_id: MOCK_THIRD_AGENT_ID, agent_name: 'Sentry' },
    ],
    unread_count: 3,
    latest_at: iso(-2 * ONE_MIN),
    messages: [
      {
        message_id: 'msg_001',
        sender_id: MOCK_THIRD_AGENT_ID,
        sender_name: 'Sentry',
        content: 'CI failure on `main` — build #4821 failed at `pytest -m integration`.\nFirst fail: `test_job_timezone_redesign`.',
        is_read: false,
        created_at: iso(-2 * ONE_MIN),
      },
      {
        message_id: 'msg_002',
        sender_id: MOCK_THIRD_AGENT_ID,
        sender_name: 'Sentry',
        content: 'Deploy rolled back to `v1.0.3-rc.2`. No user-facing impact.',
        is_read: false,
        created_at: iso(-7 * ONE_MIN),
      },
      {
        message_id: 'msg_003',
        sender_id: MOCK_THIRD_AGENT_ID,
        sender_name: 'Sentry',
        content: 'RCA ticket opened: INC-2041.',
        is_read: false,
        created_at: iso(-8 * ONE_MIN),
      },
      {
        message_id: 'msg_004',
        sender_id: MOCK_AGENT_ID,
        sender_name: 'Atlas',
        content: 'Acknowledged. I\'ll cross-reference with the midnight migration and report back.',
        is_read: true,
        created_at: iso(-1 * ONE_HOUR),
      },
    ],
  },
  {
    room_id: 'room_research',
    room_name: 'research-sync',
    members: [
      { agent_id: MOCK_AGENT_ID, agent_name: 'Atlas' },
      { agent_id: MOCK_SECOND_AGENT_ID, agent_name: 'Scribe' },
    ],
    unread_count: 1,
    latest_at: iso(-40 * ONE_MIN),
    messages: [
      {
        message_id: 'msg_101',
        sender_id: MOCK_SECOND_AGENT_ID,
        sender_name: 'Scribe',
        content: 'Meeting notes from today\'s alignment sync are ready. Tagged action items to you.',
        is_read: false,
        created_at: iso(-40 * ONE_MIN),
      },
      {
        message_id: 'msg_102',
        sender_id: MOCK_AGENT_ID,
        sender_name: 'Atlas',
        content: 'Great, thanks. Pushing the digest job to run with the new citations.',
        is_read: true,
        created_at: iso(-2 * ONE_HOUR),
      },
    ],
  },
  {
    room_id: 'room_archived',
    room_name: 'product-ideas',
    members: [
      { agent_id: MOCK_AGENT_ID, agent_name: 'Atlas' },
    ],
    unread_count: 0,
    latest_at: iso(-3 * ONE_DAY),
    messages: [
      {
        message_id: 'msg_201',
        sender_id: 'user_demo',
        sender_name: 'demo.user',
        content: 'Shelving the real-time co-editing idea for now — we\'ll revisit after Q2.',
        is_read: true,
        created_at: iso(-3 * ONE_DAY),
      },
    ],
  },
];

/* ────────────────────────────── Social network ──────── */

export const mockSocialEntities: SocialNetworkEntity[] = [
  {
    entity_id: MOCK_USER_ID,
    entity_name: 'You',
    entity_type: 'user',
    familiarity: 'direct',
    identity_info: { org: 'NetMind', role: 'Product design' },
    contact_info: { email: 'demo.user@example.com' },
    tags: ['owner'],
    keywords: ['owner'],
    relationship_strength: 1.0,
    interaction_count: 248,
    last_interaction_time: iso(-3 * ONE_MIN),
    persona: 'Prefers terse, structured replies. Dislikes excess preamble. Responds best to bullet lists under 5 items.',
    related_job_ids: ['job_a1b2c3d4', 'job_e5f6g7h8'],
    expertise_domains: ['Product design', 'Agent UX', 'Typography'],
  },
  {
    entity_id: 'entity_academic_researcher',
    entity_name: 'Prof. A. Researcher',
    aliases: ['researcher@example.edu', 'Prof. Researcher (Univ)'],
    entity_description: 'Senior researcher in RLHF and post-training alignment. Co-author on 3 of the papers in our reading list.',
    entity_type: 'contact',
    familiarity: 'direct',
    identity_info: { org: 'Example University', role: 'Associate Professor' },
    contact_info: { email: 'researcher@example.edu', phone: '+1 555 0100' },
    tags: ['academic', 'rlhf'],
    keywords: ['academic', 'rlhf'],
    relationship_strength: 0.82,
    interaction_count: 14,
    last_interaction_time: iso(-2 * ONE_DAY),
    persona: 'Formal register. Prefers responses that cite source papers explicitly.',
    related_job_ids: ['job_e5f6g7h8'],
    expertise_domains: ['RLHF', 'Alignment', 'Chinese NLP'],
  },
  {
    entity_id: 'entity_dana_product',
    entity_name: 'Dana Kim',
    entity_description: 'Product lead, drives the roadmap for the Agent Shell v2 release.',
    entity_type: 'colleague',
    familiarity: 'direct',
    identity_info: { org: 'NetMind', role: 'PM, Agent Shell' },
    contact_info: { email: 'dana.kim@example.com', slack: '@dana' },
    tags: ['internal'],
    keywords: ['internal', 'roadmap'],
    relationship_strength: 0.74,
    interaction_count: 89,
    last_interaction_time: iso(-30 * ONE_MIN),
    persona: 'Fast-paced, action-oriented. Wants one clear ask per message.',
    expertise_domains: ['Product strategy', 'Roadmapping'],
  },
  {
    entity_id: 'entity_mira',
    entity_name: 'Mira Volkov',
    entity_type: 'contact',
    familiarity: 'direct',
    identity_info: { org: 'Anthropic', role: 'Research engineer' },
    contact_info: { email: 'mira@example.com' },
    tags: ['external'],
    relationship_strength: 0.55,
    interaction_count: 6,
    last_interaction_time: iso(-5 * ONE_DAY),
    expertise_domains: ['Distributed training', 'MoE'],
  },
  {
    entity_id: 'entity_j_doe',
    entity_name: 'Jordan Doe',
    entity_type: 'contact',
    familiarity: 'known_of',
    identity_info: { org: 'Unknown', role: 'Conference contact' },
    contact_info: {},
    tags: ['lead'],
    relationship_strength: 0.22,
    interaction_count: 1,
    last_interaction_time: iso(-45 * ONE_DAY),
  },
  {
    entity_id: 'entity_ops_team',
    entity_name: 'Ops Team (shared)',
    entity_type: 'group',
    familiarity: 'direct',
    identity_info: { org: 'NetMind' },
    contact_info: { slack: '#ops' },
    tags: ['internal', 'team'],
    relationship_strength: 0.6,
    interaction_count: 32,
    last_interaction_time: iso(-6 * ONE_HOUR),
    expertise_domains: ['Infra', 'Incident response'],
  },
];

/* ────────────────────────────── Narratives + Events ── */

const mockInstances: InstanceInfo[] = [
  {
    instance_id: 'chat_8a9b',
    module_class: 'ChatModule',
    description: 'Primary chat module for Atlas',
    status: 'completed',
    dependencies: [],
    config: {},
    created_at: iso(-14 * ONE_DAY),
    user_id: MOCK_USER_ID,
  },
  {
    instance_id: 'awareness_aa01',
    module_class: 'AwarenessModule',
    description: 'Who-is-the-user tracking',
    status: 'completed',
    dependencies: [],
    config: {},
    created_at: iso(-14 * ONE_DAY),
    user_id: MOCK_USER_ID,
  },
];

export const mockNarratives: ChatHistoryNarrative[] = [
  {
    narrative_id: 'nar_7c8d9e',
    name: 'Weekly research digest',
    description: 'Recurring narrative covering the weekly ArXiv summary workflow.',
    current_summary: 'User and Atlas have established a weekly cadence for digesting alignment research. Recent focus has been on RLHF follow-ups and Prof. Researcher\'s latest preprint.',
    actors: [
      { id: MOCK_USER_ID, type: 'user' },
      { id: 'entity_academic_researcher', type: 'contact' },
    ],
    created_at: iso(-14 * ONE_DAY),
    updated_at: iso(-2 * ONE_MIN),
    instances: mockInstances,
  },
  {
    narrative_id: 'nar_ab12cd',
    name: 'Incident postmortem drafts',
    description: 'Atlas drafts postmortem skeletons from Sentry alerts.',
    current_summary: 'Three incidents last sprint — all drafted into the template. User typically edits voice/tone, not facts.',
    actors: [
      { id: MOCK_USER_ID, type: 'user' },
      { id: 'entity_ops_team', type: 'group' },
    ],
    created_at: iso(-21 * ONE_DAY),
    updated_at: iso(-1 * ONE_DAY),
    instances: [mockInstances[0]],
  },
];

export const mockChatEvents: ChatHistoryEvent[] = [
  {
    event_id: 'evt_001',
    narrative_id: 'nar_7c8d9e',
    narrative_name: 'Weekly research digest',
    trigger: 'User asked for a summary of this week\'s alignment papers',
    trigger_source: 'chat',
    user_id: MOCK_USER_ID,
    final_output: 'Drafted digest covering 12 papers; 3 flagged as high-priority read.',
    created_at: iso(-2 * ONE_MIN),
    event_log: [],
  },
  {
    event_id: 'evt_002',
    narrative_id: 'nar_7c8d9e',
    narrative_name: 'Weekly research digest',
    trigger: 'Scheduled run (cron)',
    trigger_source: 'job',
    final_output: 'Published digest to Notion. 2 subscribers notified.',
    created_at: iso(-4 * ONE_DAY),
    event_log: [],
  },
  {
    event_id: 'evt_003',
    narrative_id: 'nar_ab12cd',
    narrative_name: 'Incident postmortem drafts',
    trigger: 'Sentry alert → auto-drafted postmortem',
    trigger_source: 'lark',
    final_output: 'Skeleton posted to #incident-reviews for human review.',
    created_at: iso(-1 * ONE_DAY),
    event_log: [],
  },
];

/* ─────────────────── Simple chat history (messages) ── */

export const mockChatMessages: SimpleChatMessage[] = [
  {
    role: 'user',
    content: 'Atlas, can you give me a quick summary of this week\'s alignment papers?',
    timestamp: iso(-3 * ONE_MIN),
    narrative_id: 'nar_7c8d9e',
    working_source: 'chat',
    message_type: 'chat',
    event_id: 'evt_001',
  },
  {
    role: 'assistant',
    content:
      'Here are the 3 highest-signal papers from this week:\n\n' +
      '1. **Chen et al. 2026** — "Stabilising RLHF via gradient-variance regularisation" · introduces a single-line regulariser that drops training instability by ~40%.\n' +
      '2. **Volkov et al. 2026** — "MoE routing under distribution shift" · follow-up to last year\'s paper, now with a cleaner ablation.\n' +
      '3. **Lee 2026** — "Why step-level RM beats outcome RM for multi-turn" · empirical study on GSM8K-M.\n\n' +
      'Want me to push the full digest to Notion now, or hold until Prof. Chen replies to your email thread?',
    timestamp: iso(-2 * ONE_MIN),
    narrative_id: 'nar_7c8d9e',
    working_source: 'chat',
    message_type: 'chat',
    event_id: 'evt_001',
  },
  {
    role: 'user',
    content: 'Hold until Prof. Chen replies. In the meantime, draft the response to him for me.',
    timestamp: iso(-90 * 1000),
    narrative_id: 'nar_7c8d9e',
    working_source: 'chat',
    message_type: 'chat',
  },
  {
    content: 'Atlas created job: "Draft reply to Prof. Chen"',
    role: 'assistant',
    timestamp: iso(-80 * 1000),
    narrative_id: 'nar_7c8d9e',
    message_type: 'activity',
  },
];

/* ─────────────────────────────── Skills ──────────────── */

const skillEntries: SkillInfo[] = [
  {
    name: 'arxiv-search',
    description: 'Search ArXiv papers by keyword, author, or date range.',
    path: '/skills/arxiv-search',
    disabled: false,
    version: '0.4.1',
    author: 'NetMind',
    source_url: 'https://github.com/netmind/skill-arxiv-search',
    installed_at: iso(-7 * ONE_DAY),
    requires_env: ['ARXIV_API_KEY'],
    requires_bins: [],
    env_configured: true,
    study_status: 'completed',
    study_result: 'Agent has indexed 12 supported search filters.',
    studied_at: iso(-6 * ONE_DAY),
  },
  {
    name: 'notion-publish',
    description: 'Create/update pages in a Notion workspace. Supports markdown, tables, and inline databases.',
    path: '/skills/notion-publish',
    disabled: false,
    version: '1.2.0',
    author: 'NetMind',
    source_url: 'https://github.com/netmind/skill-notion-publish',
    installed_at: iso(-14 * ONE_DAY),
    requires_env: ['NOTION_TOKEN'],
    env_configured: true,
    study_status: 'completed',
    studied_at: iso(-14 * ONE_DAY),
  },
  {
    name: 'slack-digest',
    description: 'Aggregate messages from specified Slack channels into a daily digest.',
    path: '/skills/slack-digest',
    disabled: false,
    version: '0.9.3',
    author: 'community',
    source_url: 'https://github.com/community/skill-slack-digest',
    installed_at: iso(-3 * ONE_DAY),
    requires_env: ['SLACK_BOT_TOKEN'],
    env_configured: false,
    study_status: 'idle',
  },
  {
    name: 'legacy-transcript-import',
    description: 'Batch import Zoom / Teams transcripts. Marked for deprecation in v2.',
    path: '/skills/legacy-transcript-import',
    disabled: true,
    version: '0.1.5',
    author: 'demo.user',
    installed_at: iso(-60 * ONE_DAY),
    study_status: 'failed',
    study_error: 'Transcript parser binary missing. Install `ffmpeg` and retry.',
  },
];

export const mockSkills: SkillListResponse = {
  skills: skillEntries,
  total: skillEntries.length,
};

/* ─────────────────────────────── MCPs ────────────────── */

export const mockMCPs: MCPInfo[] = [
  {
    mcp_id: 'mcp_github',
    agent_id: MOCK_AGENT_ID,
    user_id: MOCK_USER_ID,
    name: 'GitHub',
    url: 'https://api.githubcopilot.com/mcp',
    description: 'Read/write issues, PRs, and release notes.',
    is_enabled: true,
    connection_status: 'connected',
    last_check_time: iso(-5 * ONE_MIN),
    created_at: iso(-30 * ONE_DAY),
    updated_at: iso(-5 * ONE_MIN),
  },
  {
    mcp_id: 'mcp_linear',
    agent_id: MOCK_AGENT_ID,
    user_id: MOCK_USER_ID,
    name: 'Linear',
    url: 'https://mcp.linear.app',
    description: 'Project + issue tracking.',
    is_enabled: true,
    connection_status: 'connected',
    last_check_time: iso(-12 * ONE_MIN),
    created_at: iso(-14 * ONE_DAY),
    updated_at: iso(-12 * ONE_MIN),
  },
  {
    mcp_id: 'mcp_figma',
    agent_id: MOCK_AGENT_ID,
    user_id: MOCK_USER_ID,
    name: 'Figma (dev)',
    url: 'http://localhost:3845/mcp',
    description: 'Local Figma dev MCP — only reachable from macOS.',
    is_enabled: false,
    connection_status: 'failed',
    last_check_time: iso(-2 * ONE_HOUR),
    last_error: 'ECONNREFUSED — make sure Figma desktop is running.',
    created_at: iso(-7 * ONE_DAY),
    updated_at: iso(-2 * ONE_HOUR),
  },
];

/* ─────────────────────────────── RAG files ──────────── */

export const mockRAGFiles: RAGFileInfo[] = [
  {
    filename: 'q4-2025-research-notes.md',
    size: 48_210,
    modified_at: iso(-2 * ONE_DAY),
    upload_status: 'completed',
  },
  {
    filename: 'chen-2026-rlhf-preprint.pdf',
    size: 1_240_500,
    modified_at: iso(-5 * ONE_DAY),
    upload_status: 'completed',
  },
  {
    filename: 'volkov-2026-moe.pdf',
    size: 892_300,
    modified_at: iso(-2 * ONE_HOUR),
    upload_status: 'uploading',
  },
  {
    filename: 'broken-scan.pdf',
    size: 2_048_000,
    modified_at: iso(-10 * ONE_DAY),
    upload_status: 'failed',
    error_message: 'OCR failed: unsupported PDF structure.',
  },
];

/* ─────────────────────────────── Cost summary ───────── */

export const mockCostSummary: CostSummary = {
  total_cost_usd: 12.47,
  total_input_tokens: 1_248_300,
  total_output_tokens: 218_900,
  by_model: {
    'claude-sonnet-4.6': {
      cost: 8.12,
      input_tokens: 820_000,
      output_tokens: 140_000,
      call_count: 148,
    },
    'claude-opus-4.7': {
      cost: 3.75,
      input_tokens: 248_000,
      output_tokens: 58_000,
      call_count: 22,
    },
    'claude-haiku-4.5': {
      cost: 0.60,
      input_tokens: 180_300,
      output_tokens: 20_900,
      call_count: 84,
    },
  },
  daily: Array.from({ length: 7 }, (_, i) => {
    const day = new Date(NOW - (6 - i) * ONE_DAY);
    return {
      date: day.toISOString().slice(0, 10),
      input_tokens: 100_000 + Math.floor(Math.random() * 120_000),
      output_tokens: 15_000 + Math.floor(Math.random() * 30_000),
    };
  }),
};

/* ─────────────────────────────── Awareness ──────────── */

export const mockAwareness = `
The user is a **product designer at NetMind** with a strong typographic sensibility. They are the primary stakeholder for the Agent Shell v2 release.

**Communication preferences**
- Prefers terse, structured replies. Dislikes excess preamble.
- Responds best to **bullet lists under 5 items**.
- Uses precise typographic language (kerning, optical alignment, hairline rules).

**Current focus (last 7 days)**
- Nordic-archive redesign of the Agent Shell UI
- Alignment research digest — weekly cadence on Mondays
- Prof. Chen's email thread (pending reply)

**Active projects**
- \`job_a1b2c3d4\` — Weekly research digest (scheduled, running)
- \`job_e5f6g7h8\` — Draft reply to Prof. Chen (one-off, pending)

**Known collaborators**
- Dana Kim (PM, internal) · fast-paced
- Prof. Chen Li (Tsinghua) · formal register
- Ops Team · incident response
`.trim();

/* ─────────────────────────────── Embedding status ───── */

export const mockEmbeddingStatus: EmbeddingStatusData = {
  model: 'text-embedding-3-large',
  stats: {
    narratives: { total: 2, migrated: 2, missing: 0 },
    events: { total: 3, migrated: 3, missing: 0 },
    social_entities: { total: 6, migrated: 6, missing: 0 },
  },
  all_done: true,
  migration: {
    is_running: false,
    current_model: 'text-embedding-3-large',
    total: {},
    completed: {},
    failed: {},
    total_count: 0,
    completed_count: 0,
    progress_pct: 100,
    error: null,
    finished: true,
  },
};

/* ─────────────────────────────── Dashboard ──────────── */

const ownedAtlas: OwnedAgentStatus = {
  agent_id: MOCK_AGENT_ID,
  name: 'Atlas',
  description: 'Research agent — reads papers, drafts summaries.',
  is_public: false,
  owned_by_viewer: true,
  status: {
    kind: 'JOB',
    last_activity_at: iso(-2 * ONE_MIN),
    started_at: iso(-30 * ONE_MIN),
  },
  running_count: 1,
  action_line: 'Running: weekly-research-digest · step 3 of 4',
  verb_line: 'Running: weekly-research-digest',
  sessions: [
    {
      session_id: 'sess_001',
      user_display: 'demo.user',
      channel: 'chat',
      started_at: iso(-30 * ONE_MIN),
      user_last_message_preview: 'Hold until Prof. Chen replies. In the meantime, draft the response…',
    },
  ],
  running_jobs: [
    {
      job_id: 'job_a1b2c3d4',
      title: 'Weekly research digest',
      job_type: 'scheduled',
      started_at: iso(-30 * ONE_MIN),
      description: 'ArXiv summary → Notion publish',
      progress: { current_step: 3, total_steps: 4, stage_name: 'summarise_top_10', estimated_pct: 72 },
    },
  ],
  pending_jobs: [
    {
      job_id: 'job_e5f6g7h8',
      title: 'Draft reply to Prof. Chen',
      job_type: 'one_off',
      next_run_at: localDateTime(20 * ONE_MIN),
      next_run_timezone: 'Asia/Shanghai',
      queue_status: 'pending',
    },
    {
      job_id: 'job_m3n4o5p6',
      title: 'Daily standup prep',
      job_type: 'scheduled',
      next_run_at: localDateTime(ONE_DAY - 1 * ONE_HOUR),
      next_run_timezone: 'Asia/Shanghai',
      queue_status: 'pending',
    },
  ],
  enhanced: {
    recent_errors_1h: 0,
    token_rate_1h: 18400,
    active_narratives: 2,
    unread_bus_messages: 4,
  },
  queue: { running: 1, active: 1, pending: 2, blocked: 1, paused: 1, failed: 1, total: 7 },
  recent_events: [
    { event_id: 'evt_001', kind: 'running', verb: 'summarising', target: 'Chen et al. 2026', created_at: iso(-2 * ONE_MIN) },
    { event_id: 'evt_002', kind: 'completed', verb: 'fetched', target: '12 papers', duration_ms: 3800, created_at: iso(-10 * ONE_MIN) },
    { event_id: 'evt_003', kind: 'failed', verb: 'upload', target: 'broken-scan.pdf', duration_ms: 2100, created_at: iso(-6 * ONE_HOUR) },
    { event_id: 'evt_004', kind: 'chat', verb: 'replied', target: 'demo.user', duration_ms: 1200, created_at: iso(-25 * ONE_MIN) },
  ],
  metrics_today: {
    runs_ok: 14,
    errors: 1,
    avg_duration_ms: 2800,
    avg_duration_trend: 'down',
    token_cost_cents: 124,
  },
  attention_banners: [
    {
      level: 'warning',
      kind: 'job_blocked',
      message: 'Job "Awaiting GitHub OAuth scope" has been blocked for 2 hours.',
      action: { label: 'Retry', endpoint: '/api/jobs/job_y5z6a7b8/retry', method: 'POST' },
    },
  ],
  health: 'healthy_running',
  stale_instances: [],
};

const ownedScribe: OwnedAgentStatus = {
  agent_id: MOCK_SECOND_AGENT_ID,
  name: 'Scribe',
  description: 'Note-taking & meeting summariser.',
  is_public: true,
  owned_by_viewer: true,
  status: { kind: 'idle', last_activity_at: iso(-2 * ONE_HOUR), started_at: null },
  running_count: 0,
  action_line: null,
  verb_line: 'Idle · last active 2h ago',
  sessions: [],
  running_jobs: [],
  pending_jobs: [],
  enhanced: { recent_errors_1h: 0, token_rate_1h: null, active_narratives: 1, unread_bus_messages: 1 },
  queue: { running: 0, active: 0, pending: 0, blocked: 0, paused: 0, failed: 0, total: 0 },
  recent_events: [
    { event_id: 'evt_s01', kind: 'chat', verb: 'replied', target: 'dana.kim', duration_ms: 800, created_at: iso(-2 * ONE_HOUR) },
  ],
  metrics_today: {
    runs_ok: 3, errors: 0, avg_duration_ms: 1200, avg_duration_trend: 'flat', token_cost_cents: 18,
  },
  attention_banners: [],
  health: 'healthy_idle',
  stale_instances: [],
};

const ownedSentry: OwnedAgentStatus = {
  agent_id: MOCK_THIRD_AGENT_ID,
  name: 'Sentry',
  description: 'Ops monitor — watches CI, alerts on job failures.',
  is_public: false,
  owned_by_viewer: true,
  status: { kind: 'MESSAGE_BUS', last_activity_at: iso(-2 * ONE_MIN), started_at: iso(-45 * ONE_MIN), details: { src_channel: '#ci', dst_channel: 'eng-alerts' } },
  running_count: 1,
  action_line: 'Watching: #ci → eng-alerts',
  verb_line: 'Watching: #ci',
  sessions: [],
  running_jobs: [
    { job_id: 'job_i9j0k1l2', title: 'Slack channel watcher', job_type: 'ongoing', started_at: iso(-45 * ONE_MIN), progress: null },
  ],
  pending_jobs: [],
  enhanced: { recent_errors_1h: 2, token_rate_1h: 820, active_narratives: 1, unread_bus_messages: 3 },
  queue: { running: 1, active: 0, pending: 0, blocked: 0, paused: 0, failed: 1, total: 2 },
  recent_events: [
    { event_id: 'evt_x01', kind: 'failed', verb: 'build failed', target: 'main#4821', duration_ms: 185000, created_at: iso(-2 * ONE_MIN) },
  ],
  metrics_today: { runs_ok: 28, errors: 2, avg_duration_ms: 180000, avg_duration_trend: 'up', token_cost_cents: 6 },
  attention_banners: [
    {
      level: 'error',
      kind: 'job_failed',
      message: 'Build #4821 failed on `main` — 2 test failures.',
      action: { label: 'Open logs', endpoint: '#', method: 'GET' },
    },
  ],
  health: 'warning',
  stale_instances: [],
};

const publicAgent: PublicAgentStatus = {
  agent_id: 'public_aria',
  name: 'Aria (community)',
  description: 'Shared researcher agent.',
  is_public: true,
  owned_by_viewer: false,
  status: { kind: 'CHAT', last_activity_at: iso(-5 * ONE_MIN), started_at: iso(-40 * ONE_MIN) },
  running_count_bucket: '3-5',
};

export const mockDashboardAgents: AgentStatus[] = [ownedAtlas, ownedScribe, ownedSentry, publicAgent];
