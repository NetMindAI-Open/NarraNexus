/**
 * Mock API layer — intercepts `api.*` calls in dev so the UI can
 * render realistic data without a running backend.
 *
 * Enable with `?mock=1` in URL (persists to localStorage),
 * disable with `?mock=0`, or toggle in the dev banner.
 */

import type {
  Job,
  JobListResponse,
  JobDetailResponse,
  CancelJobResponse,
  AgentInboxListResponse,
  MarkReadResponse,
  AwarenessResponse,
  ClearHistoryResponse,
  SocialNetworkResponse,
  SocialNetworkListResponse,
  SocialNetworkSearchResponse,
  ChatHistoryResponse,
  SimpleChatHistoryResponse,
  EventLogResponse,
  CreateAgentResponse,
  UpdateAgentResponse,
  DeleteAgentResponse,
  FileListResponse,
  FileUploadResponse,
  FileDeleteResponse,
  MCPListResponse,
  MCPResponse,
  MCPCreateRequest,
  MCPUpdateRequest,
  MCPValidateResponse,
  MCPValidateAllResponse,
  RAGFileListResponse,
  RAGFileUploadResponse,
  RAGFileDeleteResponse,
  CreateJobComplexRequest,
  CreateJobComplexResponse,
  LoginResponse,
  RegisterResponse,
  QuotaMeResponse,
  AgentListResponse,
  CreateUserResponse,
  UpdateTimezoneResponse,
  CostResponse,
  EmbeddingStatusResponse,
  EmbeddingRebuildResponse,
  DashboardResponse,
  ApiResponse,
  LarkCredentialResponse,
  LarkBindResponse,
  LarkAuthLoginResponse,
  LarkAuthCompleteResponse,
} from '@/types';
import type { SkillListResponse, SkillOperationResponse, SkillStudyResponse, SkillEnvConfigResponse } from '@/types/skills';

import {
  MOCK_USER_ID,
  MOCK_AGENT_ID,
  mockAgents,
  mockJobs,
  mockInboxRooms,
  mockSocialEntities,
  mockNarratives,
  mockChatEvents,
  mockChatMessages,
  mockSkills,
  mockMCPs,
  mockRAGFiles,
  mockCostSummary,
  mockAwareness,
  mockEmbeddingStatus,
  mockDashboardAgents,
} from './fixtures';

/* ─────────────────────────────── toggle ─────────── */

const STORAGE_KEY = 'narranexus.mock.enabled';

/** Read ?mock=0|1 from URL; persist to localStorage. Returns final state. */
function resolveMockFlag(): boolean {
  if (typeof window === 'undefined') return false;
  const url = new URL(window.location.href);
  const q = url.searchParams.get('mock');
  if (q === '1' || q === 'true') {
    localStorage.setItem(STORAGE_KEY, '1');
    return true;
  }
  if (q === '0' || q === 'false') {
    localStorage.removeItem(STORAGE_KEY);
    return false;
  }
  return localStorage.getItem(STORAGE_KEY) === '1';
}

export const MOCK_ENABLED = resolveMockFlag();

export function setMockEnabled(enabled: boolean) {
  if (enabled) localStorage.setItem(STORAGE_KEY, '1');
  else localStorage.removeItem(STORAGE_KEY);
  window.location.reload();
}

/* ─────────────────────────────── helpers ─────────── */

/** Tiny artificial latency so loading states render at least briefly. */
const LATENCY_MS = 120;
const delay = <T,>(value: T, ms = LATENCY_MS): Promise<T> =>
  new Promise((resolve) => setTimeout(() => resolve(value), ms));

const ok = <T,>(data: T): Promise<T> => delay(data);

/* ─────────────────────────────── mock impls ──────── */

// Mutable copies so in-session mutations (create/cancel job, etc.) are visible.
const state = {
  jobs: [...mockJobs],
  mcps: [...mockMCPs],
  ragFiles: [...mockRAGFiles],
  awareness: mockAwareness,
};

function filterJobs(status?: string): Job[] {
  if (!status || status === 'all') return state.jobs;
  if (status === 'active') {
    return state.jobs.filter((j) => ['pending', 'active', 'running'].includes(j.status));
  }
  return state.jobs.filter((j) => j.status === status);
}

export const mockApi = {
  /* ─ Jobs ─ */
  async getJobs(_agentId: string, _userId?: string, status?: string): Promise<JobListResponse> {
    const jobs = filterJobs(status);
    return ok({ success: true, jobs, count: jobs.length });
  },
  async getJob(jobId: string): Promise<JobDetailResponse> {
    const job = state.jobs.find((j) => j.job_id === jobId);
    return ok(job ? { success: true, job } : { success: false, error: 'Not found' });
  },
  async cancelJob(jobId: string): Promise<CancelJobResponse> {
    const job = state.jobs.find((j) => j.job_id === jobId);
    if (!job) return ok({ success: false, error: 'Not found' });
    const previous_status = job.status;
    job.status = 'cancelled';
    return ok({ success: true, job_id: jobId, previous_status });
  },
  async createJobComplex(_request: CreateJobComplexRequest): Promise<CreateJobComplexResponse> {
    const newJob: Job = {
      job_id: `job_${Math.random().toString(36).slice(2, 10)}`,
      agent_id: MOCK_AGENT_ID,
      user_id: MOCK_USER_ID,
      job_type: 'one_off',
      title: 'New job (mock)',
      description: 'Created via createJobComplex in mock mode.',
      status: 'pending',
      trigger_config: { trigger_type: 'manual' },
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    state.jobs = [newJob, ...state.jobs];
    return ok({ success: true, group_id: `grp_${Date.now()}`, job_ids: [newJob.job_id] });
  },
  async retryJob(jobId: string) {
    const j = state.jobs.find((x) => x.job_id === jobId);
    if (j) j.status = 'pending';
    return ok({ success: true, job_id: jobId, new_status: 'pending' });
  },
  async pauseJob(jobId: string) {
    const j = state.jobs.find((x) => x.job_id === jobId);
    if (j) j.status = 'paused';
    return ok({ success: true, job_id: jobId, new_status: 'paused' });
  },
  async resumeJob(jobId: string) {
    const j = state.jobs.find((x) => x.job_id === jobId);
    if (j) j.status = 'active';
    return ok({ success: true, job_id: jobId, new_status: 'active' });
  },
  async getJobDetail(jobId: string) {
    const job = state.jobs.find((j) => j.job_id === jobId);
    return ok({ success: !!job, job: job ?? {} });
  },
  async getSessionDetail(sessionId: string, _agentId: string) {
    return ok({ success: true, session: { session_id: sessionId, messages: [] } });
  },

  /* ─ Inbox ─ */
  async getAgentInbox(_agentId: string): Promise<AgentInboxListResponse> {
    const total_unread = mockInboxRooms.reduce((sum, r) => sum + r.unread_count, 0);
    return ok({ success: true, rooms: mockInboxRooms, total_unread });
  },
  async markAgentMessageRead(_messageId: string, _agentId: string): Promise<MarkReadResponse> {
    return ok({ success: true, marked_count: 1 });
  },

  /* ─ Awareness ─ */
  async getAwareness(_agentId: string): Promise<AwarenessResponse> {
    return ok({
      success: true,
      awareness: state.awareness,
      create_time: new Date(Date.now() - 14 * 86_400_000).toISOString(),
      update_time: new Date(Date.now() - 2 * 60_000).toISOString(),
    });
  },
  async updateAwareness(_agentId: string, awareness: string): Promise<AwarenessResponse> {
    state.awareness = awareness;
    return ok({ success: true, awareness, update_time: new Date().toISOString() });
  },

  /* ─ Social network ─ */
  async getSocialNetworkList(_agentId: string): Promise<SocialNetworkListResponse> {
    return ok({ success: true, entities: mockSocialEntities, count: mockSocialEntities.length });
  },
  async getSocialNetwork(_agentId: string, userId: string): Promise<SocialNetworkResponse> {
    const entity = mockSocialEntities.find((e) => e.entity_id === userId);
    return ok({ success: true, entity });
  },
  async searchSocialNetwork(
    _agentId: string,
    query: string,
    searchType: 'keyword' | 'semantic' = 'semantic',
  ): Promise<SocialNetworkSearchResponse> {
    const q = query.trim().toLowerCase();
    const matches = mockSocialEntities
      .filter((e) =>
        (e.entity_name ?? '').toLowerCase().includes(q) ||
        (e.entity_description ?? '').toLowerCase().includes(q) ||
        (e.tags ?? []).some((t) => t.toLowerCase().includes(q))
      )
      .map((e, i) => ({ ...e, similarity_score: 0.92 - i * 0.08 }));
    return ok({ success: true, entities: matches, count: matches.length, search_type: searchType });
  },

  /* ─ Chat history ─ */
  async getChatHistory(_agentId: string, _userId?: string): Promise<ChatHistoryResponse> {
    return ok({
      success: true,
      narratives: mockNarratives,
      events: mockChatEvents,
      narrative_count: mockNarratives.length,
      event_count: mockChatEvents.length,
    });
  },
  async getSimpleChatHistory(
    _agentId: string,
    _userId: string,
    limit: number = 20,
    offset: number = 0,
  ): Promise<SimpleChatHistoryResponse> {
    const slice = mockChatMessages.slice(offset, offset + limit);
    return ok({ success: true, messages: slice, total_count: mockChatMessages.length });
  },
  async getEventLog(_agentId: string, eventId: string): Promise<EventLogResponse> {
    return ok({
      success: true,
      event_id: eventId,
      thinking:
        'Matched 12 papers from ArXiv tagged "alignment".\n' +
        'Re-ranked by citation + author prior; Chen 2026 surfaced at rank 1.\n' +
        'Drafting digest with 3-tier priority...',
      tool_calls: [
        {
          tool_name: 'arxiv_search',
          tool_input: { query: 'RLHF alignment', since: '2026-04-15' },
          tool_output: '{"results": 12, "top_papers": ["chen-2026", "volkov-2026", "lee-2026"]}',
        },
        {
          tool_name: 'rank_papers',
          tool_input: { candidates: 12, method: 'weighted_citation' },
          tool_output: '[{"id":"chen-2026","score":0.94},{"id":"volkov-2026","score":0.87}]',
        },
      ],
    });
  },
  async clearHistory(_agentId: string, _userId?: string): Promise<ClearHistoryResponse> {
    return ok({ success: true, narrative_ids_deleted: [], narratives_count: 0, events_count: 0 });
  },

  /* ─ Auth ─ */
  async login(userId: string, _password?: string): Promise<LoginResponse> {
    return ok({ success: true, user_id: userId || MOCK_USER_ID, token: 'mock.jwt.token', role: 'user' });
  },
  async register(userId: string): Promise<RegisterResponse> {
    return ok({ success: true, user_id: userId || MOCK_USER_ID, token: 'mock.jwt.token' });
  },
  async createUser(userId: string): Promise<CreateUserResponse> {
    return ok({ success: true, user_id: userId || MOCK_USER_ID });
  },
  async updateTimezone(_userId: string, timezone: string): Promise<UpdateTimezoneResponse> {
    return ok({ success: true, timezone });
  },
  async getAgents(_userId: string): Promise<AgentListResponse> {
    return ok({ success: true, agents: mockAgents, count: mockAgents.length });
  },
  async createAgent(createdBy: string, agentName?: string, agentDescription?: string): Promise<CreateAgentResponse> {
    return ok({
      success: true,
      agent: {
        agent_id: `agent_${Math.random().toString(36).slice(2, 8)}`,
        name: agentName || 'New Agent',
        description: agentDescription,
        created_by: createdBy,
        created_at: new Date().toISOString(),
      },
    });
  },
  async updateAgent(): Promise<UpdateAgentResponse> {
    return ok({ success: true });
  },
  async deleteAgent(agentId: string): Promise<DeleteAgentResponse> {
    return ok({ success: true, agent_id: agentId });
  },

  /* ─ Files ─ */
  async listFiles(): Promise<FileListResponse> {
    // Schema switched from a flat `files` array to a recursive `tree` of
    // FileInfo in 2026-05-14 with the artifact pointer-model rework.
    const now = new Date().toISOString();
    return ok({
      success: true,
      tree: [
        { name: 'notes.md', path: 'notes.md', is_dir: false, size: 4096, modified_at: now },
        { name: 'scratch.txt', path: 'scratch.txt', is_dir: false, size: 1200, modified_at: now },
      ],
      workspace_path: '/mock/workspace',
    });
  },
  async uploadFile(_agentId: string, _userId: string, file: File): Promise<FileUploadResponse> {
    return ok({ success: true, filename: file.name, size: file.size, workspace_path: '/mock/workspace' });
  },
  async deleteFile(_agentId: string, _userId: string, filename: string): Promise<FileDeleteResponse> {
    return ok({ success: true, filename });
  },

  /* ─ MCPs ─ */
  async listMCPs(): Promise<MCPListResponse> {
    return ok({ success: true, mcps: state.mcps, count: state.mcps.length });
  },
  async createMCP(_agentId: string, _userId: string, data: MCPCreateRequest): Promise<MCPResponse> {
    const newMcp = {
      mcp_id: `mcp_${Math.random().toString(36).slice(2, 8)}`,
      agent_id: MOCK_AGENT_ID,
      user_id: MOCK_USER_ID,
      name: data.name,
      url: data.url,
      description: data.description,
      is_enabled: data.is_enabled ?? true,
      connection_status: 'unknown' as const,
      created_at: new Date().toISOString(),
    };
    state.mcps = [...state.mcps, newMcp];
    return ok({ success: true, mcp: newMcp });
  },
  async updateMCP(_agentId: string, _userId: string, mcpId: string, data: MCPUpdateRequest): Promise<MCPResponse> {
    const mcp = state.mcps.find((m) => m.mcp_id === mcpId);
    if (mcp) Object.assign(mcp, data);
    return ok({ success: true, mcp });
  },
  async deleteMCP(_agentId: string, _userId: string, mcpId: string): Promise<MCPResponse> {
    state.mcps = state.mcps.filter((m) => m.mcp_id !== mcpId);
    return ok({ success: true });
  },
  async validateMCP(_agentId: string, _userId: string, mcpId: string): Promise<MCPValidateResponse> {
    return ok({ success: true, mcp_id: mcpId, connected: true });
  },
  async validateAllMCPs(): Promise<MCPValidateAllResponse> {
    const results = state.mcps.map((m) => ({ success: true, mcp_id: m.mcp_id, connected: m.is_enabled }));
    return ok({
      success: true,
      results,
      total: results.length,
      connected: results.filter((r) => r.connected).length,
      failed: results.filter((r) => !r.connected).length,
    });
  },

  /* ─ RAG ─ */
  async listRAGFiles(): Promise<RAGFileListResponse> {
    return ok({
      success: true,
      files: state.ragFiles,
      total_count: state.ragFiles.length,
      completed_count: state.ragFiles.filter((f) => f.upload_status === 'completed').length,
      pending_count: state.ragFiles.filter((f) => f.upload_status === 'pending' || f.upload_status === 'uploading').length,
    });
  },
  async uploadRAGFile(_agentId: string, _userId: string, file: File): Promise<RAGFileUploadResponse> {
    state.ragFiles = [
      { filename: file.name, size: file.size, modified_at: new Date().toISOString(), upload_status: 'uploading' },
      ...state.ragFiles,
    ];
    return ok({ success: true, filename: file.name, size: file.size, upload_status: 'uploading' });
  },
  async deleteRAGFile(_agentId: string, _userId: string, filename: string): Promise<RAGFileDeleteResponse> {
    state.ragFiles = state.ragFiles.filter((f) => f.filename !== filename);
    return ok({ success: true, filename });
  },

  /* ─ Skills ─ */
  async listSkills(): Promise<SkillListResponse> {
    return ok(mockSkills);
  },
  async getSkill(skillName: string): Promise<SkillOperationResponse> {
    const skill = mockSkills.skills.find((s) => s.name === skillName);
    return ok({ success: !!skill, skill });
  },
  async installSkillFromGithub(): Promise<SkillOperationResponse> {
    return ok({ success: true, message: 'Installed (mock).' });
  },
  async installSkillFromZip(): Promise<SkillOperationResponse> {
    return ok({ success: true, message: 'Installed (mock).' });
  },
  async removeSkill(): Promise<SkillOperationResponse> {
    return ok({ success: true, message: 'Removed (mock).' });
  },
  async disableSkill(): Promise<SkillOperationResponse> {
    return ok({ success: true, message: 'Disabled (mock).' });
  },
  async enableSkill(): Promise<SkillOperationResponse> {
    return ok({ success: true, message: 'Enabled (mock).' });
  },
  async studySkill(): Promise<SkillStudyResponse> {
    return ok({ success: true, study_status: 'studying' });
  },
  async getSkillStudyStatus(): Promise<SkillStudyResponse> {
    return ok({ success: true, study_status: 'completed', study_result: 'Mock study completed.' });
  },
  async getSkillEnvConfig(): Promise<SkillEnvConfigResponse> {
    return ok({ success: true, requires_env: [], env_configured: {} });
  },
  async setSkillEnvConfig(): Promise<ApiResponse> {
    return ok({ success: true });
  },

  /* ─ Costs / embeddings / dashboard ─ */
  async getCosts(_agentId: string, _days: number = 7): Promise<CostResponse> {
    return ok({ success: true, summary: mockCostSummary, records: [], total_count: 0 });
  },
  async getEmbeddingStatus(): Promise<EmbeddingStatusResponse> {
    return ok({ success: true, data: mockEmbeddingStatus });
  },
  async rebuildEmbeddings(): Promise<EmbeddingRebuildResponse> {
    return ok({ success: true, message: 'Rebuild scheduled (mock).' });
  },
  async getDashboardStatus(): Promise<DashboardResponse> {
    return ok({ success: true, agents: mockDashboardAgents });
  },
  async getAgentSparkline(_agentId: string, hours = 24) {
    // 24 buckets, randomly distributed activity
    const buckets = Array.from({ length: hours }, () => Math.floor(Math.random() * 12));
    return ok({ success: true, buckets, hours });
  },

  /* ─ Quota ─ */
  async getMyQuota(): Promise<QuotaMeResponse> {
    return ok({ enabled: false });
  },
  async setQuotaPreference(): Promise<QuotaMeResponse> {
    return ok({ enabled: false });
  },

  /* ─ Lark (stub) ─ */
  async getLarkCredential(): Promise<LarkCredentialResponse> {
    return ok({ success: true, data: null });
  },
  async bindLarkBot(): Promise<LarkBindResponse> {
    return ok({ success: true });
  },
  async larkAuthLogin(): Promise<LarkAuthLoginResponse> {
    return ok({ success: true, data: {} });
  },
  async larkAuthComplete(): Promise<LarkAuthCompleteResponse> {
    return ok({ success: true, data: {} });
  },
  async getLarkAuthStatus(): Promise<ApiResponse> {
    return ok({ success: true });
  },
  async testLarkConnection(): Promise<ApiResponse> {
    return ok({ success: true });
  },
  async unbindLarkBot(): Promise<ApiResponse> {
    return ok({ success: true });
  },
};

export type MockApi = typeof mockApi;
