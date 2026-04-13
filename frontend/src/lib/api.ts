/**
 * API client for backend communication
 * Uses relative paths in dev (Vite proxy) and configurable base URL in production
 */

import type {
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
  AgentListResponse,
  CreateUserResponse,
  UpdateTimezoneResponse,
  SkillListResponse,
  SkillOperationResponse,
  SkillStudyResponse,
  CostResponse,
  SkillEnvConfigResponse,
  EmbeddingStatusResponse,
  EmbeddingRebuildResponse,
  DashboardResponse,
} from '@/types';

// Base URL resolution is delegated to runtimeStore.getApiBaseUrl() so
// every request picks up the CURRENT mode/cloudApiUrl. See runtimeStore.ts
// for resolution order. This export is kept for backwards compatibility.
export { getApiBaseUrl as getBaseUrl } from '@/stores/runtimeStore';
import { getApiBaseUrl } from '@/stores/runtimeStore';

class ApiClient {
  private getAuthHeaders(): Record<string, string> {
    // Read JWT token from configStore (localStorage)
    try {
      const raw = localStorage.getItem('narra-nexus-config');
      if (raw) {
        const config = JSON.parse(raw);
        const token = config?.state?.token;
        if (token) {
          return { 'Authorization': `Bearer ${token}` };
        }
      }
    } catch {}
    return {};
  }

  private async request<T>(endpoint: string, options?: RequestInit): Promise<T> {
    // Resolve baseUrl fresh on every call — no caching, so mode switches
    // take effect immediately without requiring a page reload.
    const baseUrl = getApiBaseUrl();
    const url = `${baseUrl}${endpoint}`;
    const response = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...this.getAuthHeaders(),
        ...options?.headers,
      },
    });

    if (!response.ok) {
      throw new Error(`API error: ${response.status} ${response.statusText}`);
    }

    return response.json();
  }

  // Jobs API
  async getJobs(agentId: string, userId?: string, status?: string): Promise<JobListResponse> {
    let url = `/api/jobs?agent_id=${encodeURIComponent(agentId)}`;
    if (userId) url += `&user_id=${encodeURIComponent(userId)}`;
    if (status && status !== 'all') url += `&status=${encodeURIComponent(status)}`;
    return this.request<JobListResponse>(url);
  }

  async getJob(jobId: string): Promise<JobDetailResponse> {
    return this.request<JobDetailResponse>(`/api/jobs/${encodeURIComponent(jobId)}`);
  }

  async cancelJob(jobId: string): Promise<CancelJobResponse> {
    return this.request<CancelJobResponse>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: 'PUT',
    });
  }

  async createJobComplex(request: CreateJobComplexRequest): Promise<CreateJobComplexResponse> {
    return this.request<CreateJobComplexResponse>('/api/jobs/complex', {
      method: 'POST',
      body: JSON.stringify(request),
    });
  }

  // Agent Inbox API (MessageBus channel messages)
  async getAgentInbox(agentId: string, isRead?: boolean, limit?: number): Promise<AgentInboxListResponse> {
    let url = `/api/agent-inbox?agent_id=${encodeURIComponent(agentId)}`;
    if (isRead !== undefined) url += `&is_read=${isRead}`;
    if (limit !== undefined) url += `&limit=${limit}`;
    return this.request<AgentInboxListResponse>(url);
  }

  async markAgentMessageRead(messageId: string): Promise<MarkReadResponse> {
    return this.request<MarkReadResponse>(
      `/api/agent-inbox/${encodeURIComponent(messageId)}/read`,
      { method: 'PUT' }
    );
  }

  // Agents API
  async getAwareness(agentId: string): Promise<AwarenessResponse> {
    return this.request<AwarenessResponse>(`/api/agents/${encodeURIComponent(agentId)}/awareness`);
  }

  async updateAwareness(agentId: string, awareness: string): Promise<AwarenessResponse> {
    return this.request<AwarenessResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/awareness`,
      {
        method: 'PUT',
        body: JSON.stringify({ awareness }),
      }
    );
  }

  async getSocialNetwork(agentId: string, userId: string): Promise<SocialNetworkResponse> {
    return this.request<SocialNetworkResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/social-network/${encodeURIComponent(userId)}`
    );
  }

  async getSocialNetworkList(agentId: string): Promise<SocialNetworkListResponse> {
    return this.request<SocialNetworkListResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/social-network`
    );
  }

  // 语义搜索 Social Network Entities
  async searchSocialNetwork(
    agentId: string,
    query: string,
    searchType: 'keyword' | 'semantic' = 'semantic',
    limit: number = 10
  ): Promise<SocialNetworkSearchResponse> {
    const params = new URLSearchParams({
      query,
      search_type: searchType,
      limit: limit.toString(),
    });
    return this.request<SocialNetworkSearchResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/social-network/search?${params}`
    );
  }

  async getChatHistory(agentId: string, userId?: string): Promise<ChatHistoryResponse> {
    let url = `/api/agents/${encodeURIComponent(agentId)}/chat-history`;
    if (userId) url += `?user_id=${encodeURIComponent(userId)}`;
    return this.request<ChatHistoryResponse>(url);
  }

  async getSimpleChatHistory(agentId: string, userId: string, limit: number = 20, offset: number = 0): Promise<SimpleChatHistoryResponse> {
    const params = new URLSearchParams({
      user_id: userId,
      limit: limit.toString(),
      offset: offset.toString(),
    });
    return this.request<SimpleChatHistoryResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/simple-chat-history?${params}`
    );
  }

  async getEventLog(agentId: string, eventId: string): Promise<EventLogResponse> {
    return this.request<EventLogResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/event-log/${encodeURIComponent(eventId)}`
    );
  }

  async clearHistory(agentId: string, userId?: string): Promise<ClearHistoryResponse> {
    let url = `/api/agents/${encodeURIComponent(agentId)}/history`;
    if (userId) url += `?user_id=${encodeURIComponent(userId)}`;
    return this.request<ClearHistoryResponse>(url, { method: 'DELETE' });
  }

  // Auth API
  async login(userId: string, password?: string): Promise<LoginResponse> {
    return this.request<LoginResponse>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ user_id: userId, password: password || undefined }),
    });
  }

  async register(userId: string, password: string, inviteCode: string, displayName?: string): Promise<LoginResponse> {
    return this.request<LoginResponse>('/api/auth/register', {
      method: 'POST',
      body: JSON.stringify({
        user_id: userId,
        password: password,
        invite_code: inviteCode,
        display_name: displayName || undefined,
      }),
    });
  }

  async createUser(userId: string, displayName?: string): Promise<CreateUserResponse> {
    return this.request<CreateUserResponse>('/api/auth/create-user', {
      method: 'POST',
      body: JSON.stringify({
        user_id: userId,
        display_name: displayName,
      }),
    });
  }

  async updateTimezone(userId: string, timezone: string): Promise<UpdateTimezoneResponse> {
    return this.request<UpdateTimezoneResponse>('/api/auth/timezone', {
      method: 'POST',
      body: JSON.stringify({
        user_id: userId,
        timezone: timezone,
      }),
    });
  }

  async getAgents(userId: string): Promise<AgentListResponse> {
    return this.request<AgentListResponse>(
      `/api/auth/agents?user_id=${encodeURIComponent(userId)}`
    );
  }

  async createAgent(createdBy: string, agentName?: string, agentDescription?: string): Promise<CreateAgentResponse> {
    return this.request<CreateAgentResponse>('/api/auth/agents', {
      method: 'POST',
      body: JSON.stringify({
        created_by: createdBy,
        agent_name: agentName,
        agent_description: agentDescription,
      }),
    });
  }

  async updateAgent(
    agentId: string,
    agentName?: string,
    agentDescription?: string,
    isPublic?: boolean,
  ): Promise<UpdateAgentResponse> {
    return this.request<UpdateAgentResponse>(`/api/auth/agents/${encodeURIComponent(agentId)}`, {
      method: 'PUT',
      body: JSON.stringify({
        agent_name: agentName,
        agent_description: agentDescription,
        is_public: isPublic,
      }),
    });
  }

  async deleteAgent(agentId: string, userId: string): Promise<DeleteAgentResponse> {
    return this.request<DeleteAgentResponse>(
      `/api/auth/agents/${encodeURIComponent(agentId)}?user_id=${encodeURIComponent(userId)}`,
      { method: 'DELETE' }
    );
  }

  // File Management API
  async listFiles(agentId: string, userId: string): Promise<FileListResponse> {
    return this.request<FileListResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/files?user_id=${encodeURIComponent(userId)}`
    );
  }

  async uploadFile(agentId: string, userId: string, file: File): Promise<FileUploadResponse> {
    const formData = new FormData();
    formData.append('file', file);

    const url = `${getApiBaseUrl()}/api/agents/${encodeURIComponent(agentId)}/files?user_id=${encodeURIComponent(userId)}`;
    const response = await fetch(url, {
      method: 'POST',
      body: formData,
      headers: this.getAuthHeaders(),
      // Don't set Content-Type header - browser will set it with boundary for FormData
    });

    if (!response.ok) {
      throw new Error(`API error: ${response.status} ${response.statusText}`);
    }

    return response.json();
  }

  async deleteFile(agentId: string, userId: string, filename: string): Promise<FileDeleteResponse> {
    return this.request<FileDeleteResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/files/${encodeURIComponent(filename)}?user_id=${encodeURIComponent(userId)}`,
      { method: 'DELETE' }
    );
  }

  // MCP Management API
  async listMCPs(agentId: string, userId: string): Promise<MCPListResponse> {
    return this.request<MCPListResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/mcps?user_id=${encodeURIComponent(userId)}`
    );
  }

  async createMCP(agentId: string, userId: string, data: MCPCreateRequest): Promise<MCPResponse> {
    return this.request<MCPResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/mcps?user_id=${encodeURIComponent(userId)}`,
      {
        method: 'POST',
        body: JSON.stringify(data),
      }
    );
  }

  async updateMCP(agentId: string, userId: string, mcpId: string, data: MCPUpdateRequest): Promise<MCPResponse> {
    return this.request<MCPResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/mcps/${encodeURIComponent(mcpId)}?user_id=${encodeURIComponent(userId)}`,
      {
        method: 'PUT',
        body: JSON.stringify(data),
      }
    );
  }

  async deleteMCP(agentId: string, userId: string, mcpId: string): Promise<MCPResponse> {
    return this.request<MCPResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/mcps/${encodeURIComponent(mcpId)}?user_id=${encodeURIComponent(userId)}`,
      { method: 'DELETE' }
    );
  }

  async validateMCP(agentId: string, userId: string, mcpId: string): Promise<MCPValidateResponse> {
    return this.request<MCPValidateResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/mcps/${encodeURIComponent(mcpId)}/validate?user_id=${encodeURIComponent(userId)}`,
      { method: 'POST' }
    );
  }

  async validateAllMCPs(agentId: string, userId: string): Promise<MCPValidateAllResponse> {
    return this.request<MCPValidateAllResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/mcps/validate-all?user_id=${encodeURIComponent(userId)}`,
      { method: 'POST' }
    );
  }

  // RAG File Management API
  async listRAGFiles(agentId: string, userId: string): Promise<RAGFileListResponse> {
    return this.request<RAGFileListResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/rag-files?user_id=${encodeURIComponent(userId)}`
    );
  }

  async uploadRAGFile(agentId: string, userId: string, file: File): Promise<RAGFileUploadResponse> {
    const formData = new FormData();
    formData.append('file', file);

    const url = `${getApiBaseUrl()}/api/agents/${encodeURIComponent(agentId)}/rag-files?user_id=${encodeURIComponent(userId)}`;
    const response = await fetch(url, {
      method: 'POST',
      body: formData,
      headers: this.getAuthHeaders(),
      // Don't set Content-Type header - browser will set it with boundary for FormData
    });

    if (!response.ok) {
      throw new Error(`API error: ${response.status} ${response.statusText}`);
    }

    return response.json();
  }

  async deleteRAGFile(agentId: string, userId: string, filename: string): Promise<RAGFileDeleteResponse> {
    return this.request<RAGFileDeleteResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/rag-files/${encodeURIComponent(filename)}?user_id=${encodeURIComponent(userId)}`,
      { method: 'DELETE' }
    );
  }

  // Skills Management API
  async listSkills(agentId: string, userId: string, includeDisabled: boolean = false): Promise<SkillListResponse> {
    const params = new URLSearchParams({
      agent_id: agentId,
      user_id: userId,
      include_disabled: includeDisabled.toString(),
    });
    return this.request<SkillListResponse>(`/api/skills?${params}`);
  }

  async getSkill(skillName: string, agentId: string, userId: string): Promise<SkillOperationResponse> {
    const params = new URLSearchParams({
      agent_id: agentId,
      user_id: userId,
    });
    return this.request<SkillOperationResponse>(
      `/api/skills/${encodeURIComponent(skillName)}?${params}`
    );
  }

  async installSkillFromGithub(
    agentId: string,
    userId: string,
    url: string,
    branch: string = 'main'
  ): Promise<SkillOperationResponse> {
    const formData = new FormData();
    formData.append('agent_id', agentId);
    formData.append('user_id', userId);
    formData.append('source', 'github');
    formData.append('url', url);
    formData.append('branch', branch);

    const response = await fetch(`${getApiBaseUrl()}/api/skills/install`, {
      method: 'POST',
      body: formData,
      headers: this.getAuthHeaders(),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `API error: ${response.status} ${response.statusText}`);
    }

    return response.json();
  }

  async installSkillFromZip(
    agentId: string,
    userId: string,
    file: File
  ): Promise<SkillOperationResponse> {
    const formData = new FormData();
    formData.append('agent_id', agentId);
    formData.append('user_id', userId);
    formData.append('source', 'zip');
    formData.append('file', file);

    const response = await fetch(`${getApiBaseUrl()}/api/skills/install`, {
      method: 'POST',
      body: formData,
      headers: this.getAuthHeaders(),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `API error: ${response.status} ${response.statusText}`);
    }

    return response.json();
  }

  async removeSkill(
    skillName: string,
    agentId: string,
    userId: string
  ): Promise<SkillOperationResponse> {
    const params = new URLSearchParams({
      agent_id: agentId,
      user_id: userId,
    });
    return this.request<SkillOperationResponse>(
      `/api/skills/${encodeURIComponent(skillName)}?${params}`,
      { method: 'DELETE' }
    );
  }

  async disableSkill(
    skillName: string,
    agentId: string,
    userId: string
  ): Promise<SkillOperationResponse> {
    const params = new URLSearchParams({
      agent_id: agentId,
      user_id: userId,
    });
    return this.request<SkillOperationResponse>(
      `/api/skills/${encodeURIComponent(skillName)}/disable?${params}`,
      { method: 'PUT' }
    );
  }

  async enableSkill(
    skillName: string,
    agentId: string,
    userId: string
  ): Promise<SkillOperationResponse> {
    const params = new URLSearchParams({
      agent_id: agentId,
      user_id: userId,
    });
    return this.request<SkillOperationResponse>(
      `/api/skills/${encodeURIComponent(skillName)}/enable?${params}`,
      { method: 'PUT' }
    );
  }

  // Cost API
  async getCosts(agentId: string, days: number = 7): Promise<CostResponse> {
    const params = new URLSearchParams({ days: days.toString() });
    return this.request<CostResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/costs?${params}`
    );
  }

  // Skill Study API
  async studySkill(
    skillName: string,
    agentId: string,
    userId: string
  ): Promise<SkillStudyResponse> {
    const params = new URLSearchParams({
      agent_id: agentId,
      user_id: userId,
    });
    return this.request<SkillStudyResponse>(
      `/api/skills/${encodeURIComponent(skillName)}/study?${params}`,
      { method: 'POST' }
    );
  }

  async getSkillStudyStatus(
    skillName: string,
    agentId: string,
    userId: string
  ): Promise<SkillStudyResponse> {
    const params = new URLSearchParams({
      agent_id: agentId,
      user_id: userId,
    });
    return this.request<SkillStudyResponse>(
      `/api/skills/${encodeURIComponent(skillName)}/study?${params}`
    );
  }

  // Skill Env Config API
  async getSkillEnvConfig(
    skillName: string,
    agentId: string,
    userId: string
  ): Promise<SkillEnvConfigResponse> {
    const params = new URLSearchParams({
      agent_id: agentId,
      user_id: userId,
    });
    return this.request<SkillEnvConfigResponse>(
      `/api/skills/${encodeURIComponent(skillName)}/env?${params}`
    );
  }

  async setSkillEnvConfig(
    skillName: string,
    agentId: string,
    userId: string,
    envConfig: Record<string, string>
  ): Promise<SkillEnvConfigResponse> {
    const params = new URLSearchParams({
      agent_id: agentId,
      user_id: userId,
    });
    return this.request<SkillEnvConfigResponse>(
      `/api/skills/${encodeURIComponent(skillName)}/env?${params}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ env_config: envConfig }),
      }
    );
  }
  // Embedding Status API
  async getEmbeddingStatus(): Promise<EmbeddingStatusResponse> {
    return this.request<EmbeddingStatusResponse>('/api/providers/embeddings/status');
  }

  async rebuildEmbeddings(): Promise<EmbeddingRebuildResponse> {
    return this.request<EmbeddingRebuildResponse>('/api/providers/embeddings/rebuild', {
      method: 'POST',
    });
  }

  /**
   * Fetch aggregated agent status for the Dashboard page (v2).
   *
   * Viewer identity is derived server-side from the session (JWT in cloud
   * mode, local singleton user in local mode). The client MUST NOT pass a
   * `user_id` param — the backend rejects it with 400 (TDR-12).
   */
  async getDashboardStatus(): Promise<DashboardResponse> {
    return this.request<DashboardResponse>('/api/dashboard/agents-status');
  }
}

export const api = new ApiClient();
