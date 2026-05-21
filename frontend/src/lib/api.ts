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
  RegisterResponse,
  QuotaMeResponse,
  AgentListResponse,
  CreateUserResponse,
  UpdateTimezoneResponse,
  OnboardingResponse,
  SkillListResponse,
  SkillOperationResponse,
  SkillStudyResponse,
  CostResponse,
  SkillEnvConfigResponse,
  EmbeddingStatusResponse,
  EmbeddingRebuildResponse,
  DashboardResponse,
  ApiResponse,
  LarkCredentialResponse,
  LarkBindResponse,
  LarkAuthLoginResponse,
  LarkAuthCompleteResponse,
  TeamListResponse,
  TeamOperationResponse,
  BundleExportRequest,
  BundlePreflightResponse,
  BundleConfirmResponse,
  BundleArtifactPreview,
  BundleMcpPreview,
  SkillArchiveRecord,
  SlackCredentialResponse,
  SlackBindResponse,
  SlackTestResponse,
  TelegramCredentialResponse,
  TelegramBindResponse,
  TelegramTestResponse,
} from '@/types';

// Base URL resolution is delegated to runtimeStore.getApiBaseUrl() so
// every request picks up the CURRENT mode/cloudApiUrl. See runtimeStore.ts
// for resolution order. This export is kept for backwards compatibility.
export { getApiBaseUrl as getBaseUrl } from '@/stores/runtimeStore';
import { getApiBaseUrl } from '@/stores/runtimeStore';

class ApiClient {
  private getAuthHeaders(): Record<string, string> {
    // Read identity from configStore (localStorage).
    //
    // Two headers, mutually compatible:
    //   - Authorization: Bearer <jwt>  — cloud mode, signed identity
    //   - X-User-Id: <user_id>         — local mode, unsigned identity
    //
    // We send both whenever they're available; backend auth_middleware
    // decides which one to trust:
    //   - cloud mode: only JWT, X-User-Id is ignored (defence in depth)
    //   - local mode: only X-User-Id; JWT is irrelevant (no signing key)
    //
    // The single ApiClient is mode-agnostic for the same reason — the
    // mode switch happens server-side in auth_middleware, not here.
    const headers: Record<string, string> = {};
    try {
      const raw = localStorage.getItem('narra-nexus-config');
      if (raw) {
        const config = JSON.parse(raw);
        const token = config?.state?.token;
        const userId = config?.state?.userId;
        if (token) {
          headers['Authorization'] = `Bearer ${token}`;
        }
        if (userId) {
          headers['X-User-Id'] = userId;
        }
      }
    } catch {
      /* localStorage may be unavailable / disabled — fall through */
    }
    return headers;
  }

  private async request<T>(endpoint: string, options?: RequestInit): Promise<T> {
    // Resolve baseUrl fresh on every call — no caching, so mode switches
    // take effect immediately without requiring a page reload.
    const baseUrl = getApiBaseUrl();
    const url = `${baseUrl}${endpoint}`;
    const authHeaders = this.getAuthHeaders();
    const response = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders,
        ...options?.headers,
      },
    });

    if (!response.ok) {
      // Stale/expired JWT: backend says 401 even though we attached a token.
      // Tell the app to clear auth state and bounce to /login. We skip when
      // no token was attached (anonymous probe) and on auth endpoints
      // themselves (wrong-credentials login should surface in the form, not
      // log the user out of a session they never had). Decoupled via event
      // to avoid a circular import with @/stores/configStore.
      if (response.status === 401 && authHeaders.Authorization) {
        const isAuthEndpoint =
          endpoint.startsWith('/api/auth/login') ||
          endpoint.startsWith('/api/auth/register');
        if (!isAuthEndpoint) {
          window.dispatchEvent(new CustomEvent('narranexus:auth-expired'));
        }
      }
      // System free-tier quota exhausted: dispatch a global event so
      // any listener (App shell, dedicated toast, etc.) can surface it.
      // Using CustomEvent keeps api.ts UI-framework-agnostic.
      if (response.status === 402) {
        try {
          const body = await response.clone().json();
          if (body?.error_code === 'QUOTA_EXCEEDED_NO_USER_PROVIDER') {
            window.dispatchEvent(
              new CustomEvent('narranexus:quota-exceeded', {
                detail: body,
              })
            );
          }
        } catch {
          // ignore parse errors; still throw below
        }
      }
      // Extract the FastAPI HTTPException `detail` field so callers get
      // an actionable message ("Cannot add another user's agent") instead
      // of just "API error: 403 Forbidden". Falls back to the status
      // line if the body is missing / not JSON / has no `detail`.
      let detail = '';
      try {
        const body = await response.clone().json();
        if (typeof body?.detail === 'string') {
          detail = body.detail;
        } else if (body?.detail) {
          detail = JSON.stringify(body.detail);
        }
      } catch {
        /* not JSON — fall through to status line */
      }
      const label = detail
        ? `API error ${response.status}: ${detail}`
        : `API error: ${response.status} ${response.statusText}`;
      throw new Error(label);
    }

    return response.json();
  }

  // Jobs API. Identity scoped server-side from X-User-Id / JWT.
  async getJobs(agentId: string, status?: string): Promise<JobListResponse> {
    let url = `/api/jobs?agent_id=${encodeURIComponent(agentId)}`;
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

  async markAgentMessageRead(messageId: string, agentId: string): Promise<MarkReadResponse> {
    return this.request<MarkReadResponse>(
      `/api/agent-inbox/${encodeURIComponent(messageId)}/read?agent_id=${encodeURIComponent(agentId)}`,
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

  /** Fetch chat history (narratives + events) for an agent.
   *
   * @param eventLimit  optional override for how many recent events to
   *                    return. The backend defaults to 50; pass `0` to
   *                    disable the limit entirely (returns ALL events
   *                    across all narratives). Used by BundleExportPage
   *                    to enumerate every event in a narrative so the
   *                    user can toggle each one individually.
   */
  async getChatHistory(
    agentId: string,
    eventLimit?: number,
  ): Promise<ChatHistoryResponse> {
    const qs =
      eventLimit !== undefined ? `?event_limit=${eventLimit}` : '';
    return this.request<ChatHistoryResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/chat-history${qs}`
    );
  }

  async getSimpleChatHistory(agentId: string, limit: number = 20, offset: number = 0): Promise<SimpleChatHistoryResponse> {
    const params = new URLSearchParams({
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

  async clearHistory(agentId: string): Promise<ClearHistoryResponse> {
    return this.request<ClearHistoryResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/history`,
      { method: 'DELETE' },
    );
  }

  // Auth API
  async login(userId: string, password?: string): Promise<LoginResponse> {
    return this.request<LoginResponse>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ user_id: userId, password: password || undefined }),
    });
  }

  async register(userId: string, password: string, inviteCode: string, displayName?: string): Promise<RegisterResponse> {
    return this.request<RegisterResponse>('/api/auth/register', {
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

  /** New-user onboarding checklist state (cloud version). */
  async getOnboarding(userId: string): Promise<OnboardingResponse> {
    return this.request<OnboardingResponse>(
      `/api/auth/onboarding?user_id=${encodeURIComponent(userId)}`,
    );
  }

  /** Mark a single onboarding step complete. Write-once-true on the
   *  backend — passing a step here can only ever set it, never clear it. */
  async markOnboardingStep(
    userId: string,
    step: 'first_agent_created' | 'template_applied' | 'dismissed',
  ): Promise<OnboardingResponse> {
    return this.request<OnboardingResponse>('/api/auth/onboarding', {
      method: 'POST',
      body: JSON.stringify({ user_id: userId, [step]: true }),
    });
  }

  async getAgents(): Promise<AgentListResponse> {
    return this.request<AgentListResponse>(`/api/auth/agents`);
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

  async deleteAgent(agentId: string): Promise<DeleteAgentResponse> {
    return this.request<DeleteAgentResponse>(
      `/api/auth/agents/${encodeURIComponent(agentId)}`,
      { method: 'DELETE' }
    );
  }

  // File Management API — identity comes from headers (X-User-Id local, JWT cloud).
  async listFiles(agentId: string): Promise<FileListResponse> {
    return this.request<FileListResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/files`
    );
  }

  async uploadFile(agentId: string, file: File): Promise<FileUploadResponse> {
    const formData = new FormData();
    formData.append('file', file);

    const url = `${getApiBaseUrl()}/api/agents/${encodeURIComponent(agentId)}/files`;
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

  /**
   * Pre-flight check used by ChatPanel to decide whether the mic button
   * records on click or pops a "configure a provider" dialog. Walks the
   * same resolver chain the upload route uses, so a true here means
   * the very next upload will run Whisper (barring a race where the
   * user deletes their provider between mount and click).
   *
   * Returns:
   *   available: whether ANY transcription candidate exists.
   *   reason:    one of "has_openai" | "has_netmind" | "has_other"
   *              | "system_free_tier" | "none". Used to vary the
   *              dialog wording (e.g. free-tier users get "no setup
   *              required" copy) and for analytics.
   */
  async getTranscriptionAvailability(): Promise<{ available: boolean; reason: string }> {
    const url = `${getApiBaseUrl()}/api/transcription/availability`;
    const response = await fetch(url, { headers: this.getAuthHeaders() });
    if (!response.ok) {
      // Don't block the chat UI on a probe failure — fall back to
      // "available" so the existing post-upload banner takes over.
      return { available: true, reason: 'unknown' };
    }
    return response.json();
  }

  async uploadAttachment(
    agentId: string,
    file: File,
    options?: {
      /**
       * 'recording' tells the backend the file came from the in-browser
       * AudioRecorder and should be Whisper-transcribed. Anything else
       * (default) means a regular file upload — Paperclip, drag-drop,
       * paste — and the backend skips Whisper even for audio MIME
       * types. This separates "I'm dictating a message" from "here's
       * an audio file I want to share with the agent".
       */
      source?: 'recording' | 'upload';
    },
  ): Promise<{
    success: boolean;
    file_id?: string;
    mime_type?: string;
    original_name?: string;
    size_bytes?: number;
    category?: 'image' | 'document' | 'code' | 'data' | 'media' | 'other';
    // Echoed-back source: 'recording' for in-browser voice memos,
    // 'upload' for everything else (Paperclip / drag-drop / paste,
    // including regular audio file uploads). Used by the UI to choose
    // between VoiceTranscript and the standard file chip.
    source?: 'recording' | 'upload' | null;
    // Whisper output for audio/* uploads regardless of source — the
    // backend transcribes both voice memos and uploaded audio files
    // so the agent always receives the spoken content via the system
    // prompt. The frontend uses `source` (not `transcript` presence)
    // to decide rendering: voice memos surface the transcript inline,
    // file uploads keep it hidden behind the file chip.
    transcript?: string | null;
    // Per-user capability check — false means no OpenAI-compatible
    // provider configured. The UI surfaces a "voice unavailable"
    // notice only when this is false on a recording-source upload.
    transcription_available?: boolean | null;
    error?: string;
  }> {
    const formData = new FormData();
    formData.append('file', file);

    const params = new URLSearchParams();
    if (options?.source) params.set('source', options.source);
    const qs = params.toString();
    const url = `${getApiBaseUrl()}/api/agents/${encodeURIComponent(agentId)}/attachments${qs ? `?${qs}` : ''}`;
    const response = await fetch(url, {
      method: 'POST',
      body: formData,
      headers: this.getAuthHeaders(),
    });

    if (!response.ok) {
      throw new Error(`API error: ${response.status} ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * Fetch a JWT-protected attachment as a Blob.
   *
   * Used by `useAttachmentBlobUrl` to build a browser-local `blob:` URL
   * that <img>/<a> can consume without sending an Authorization header
   * (which the HTML elements can't attach themselves). Bypasses the
   * shared `request<T>` because the response body is binary.
   */
  async fetchAttachmentBlob(agentId: string, fileId: string): Promise<Blob> {
    const url = `${getApiBaseUrl()}/api/agents/${encodeURIComponent(agentId)}/attachments/${encodeURIComponent(fileId)}/raw`;
    const response = await fetch(url, {
      method: 'GET',
      headers: this.getAuthHeaders(),
    });
    if (!response.ok) {
      throw new Error(`API error: ${response.status} ${response.statusText}`);
    }
    return response.blob();
  }

  async deleteFile(agentId: string, path: string): Promise<FileDeleteResponse> {
    // Path may contain slashes (nested workspace path). encodeURI preserves
    // them while still encoding spaces / unicode. The `{path:path}` route
    // pattern on the backend accepts the full sub-path as one segment.
    return this.request<FileDeleteResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/files/${encodeURI(path)}`,
      { method: 'DELETE' }
    );
  }

  /**
   * Build a download / preview URL for a workspace file. Returns a string so
   * callers can hand it to <a href download> or fetch it for inline preview.
   * The route is JWT/X-User-Id-authed via the global middleware; <a> elements
   * load with the page's cookie context. Identity now comes only from headers
   * — never from the URL — so this helper no longer takes a user_id arg.
   */
  workspaceFileRawUrl(agentId: string, path: string): string {
    return `${getApiBaseUrl()}/api/agents/${encodeURIComponent(agentId)}/files/raw?path=${encodeURIComponent(path)}`;
  }

  /**
   * Fetch a workspace file's bytes as a Blob (JWT attached). Use for inline
   * preview or for cloud-mode downloads where <a download> can't carry auth.
   */
  async fetchWorkspaceFileBlob(agentId: string, path: string): Promise<Blob> {
    const url = this.workspaceFileRawUrl(agentId, path);
    const response = await fetch(url, { headers: this.getAuthHeaders() });
    if (!response.ok) {
      throw new Error(`API error: ${response.status} ${response.statusText}`);
    }
    return response.blob();
  }

  // MCP Management API — identity from X-User-Id / JWT headers.
  async listMCPs(agentId: string): Promise<MCPListResponse> {
    return this.request<MCPListResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/mcps`
    );
  }

  async createMCP(agentId: string, data: MCPCreateRequest): Promise<MCPResponse> {
    return this.request<MCPResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/mcps`,
      {
        method: 'POST',
        body: JSON.stringify(data),
      }
    );
  }

  async updateMCP(agentId: string, mcpId: string, data: MCPUpdateRequest): Promise<MCPResponse> {
    return this.request<MCPResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/mcps/${encodeURIComponent(mcpId)}`,
      {
        method: 'PUT',
        body: JSON.stringify(data),
      }
    );
  }

  async deleteMCP(agentId: string, mcpId: string): Promise<MCPResponse> {
    return this.request<MCPResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/mcps/${encodeURIComponent(mcpId)}`,
      { method: 'DELETE' }
    );
  }

  async validateMCP(agentId: string, mcpId: string): Promise<MCPValidateResponse> {
    return this.request<MCPValidateResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/mcps/${encodeURIComponent(mcpId)}/validate`,
      { method: 'POST' }
    );
  }

  async validateAllMCPs(agentId: string): Promise<MCPValidateAllResponse> {
    return this.request<MCPValidateAllResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/mcps/validate-all`,
      { method: 'POST' }
    );
  }

  // RAG File Management API — identity from X-User-Id / JWT headers.
  async listRAGFiles(agentId: string): Promise<RAGFileListResponse> {
    return this.request<RAGFileListResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/rag-files`
    );
  }

  async uploadRAGFile(agentId: string, file: File): Promise<RAGFileUploadResponse> {
    const formData = new FormData();
    formData.append('file', file);

    const url = `${getApiBaseUrl()}/api/agents/${encodeURIComponent(agentId)}/rag-files`;
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

  async deleteRAGFile(agentId: string, filename: string): Promise<RAGFileDeleteResponse> {
    return this.request<RAGFileDeleteResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/rag-files/${encodeURIComponent(filename)}`,
      { method: 'DELETE' }
    );
  }

  // Skills Management API — identity from X-User-Id / JWT headers.
  async listSkills(agentId: string, includeDisabled: boolean = false): Promise<SkillListResponse> {
    const params = new URLSearchParams({
      agent_id: agentId,
      include_disabled: includeDisabled.toString(),
    });
    return this.request<SkillListResponse>(`/api/skills?${params}`);
  }

  async getSkill(skillName: string, agentId: string): Promise<SkillOperationResponse> {
    const params = new URLSearchParams({ agent_id: agentId });
    return this.request<SkillOperationResponse>(
      `/api/skills/${encodeURIComponent(skillName)}?${params}`
    );
  }

  async installSkillFromGithub(
    agentId: string,
    url: string,
    branch: string = 'main'
  ): Promise<SkillOperationResponse> {
    const formData = new FormData();
    formData.append('agent_id', agentId);
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
    file: File
  ): Promise<SkillOperationResponse> {
    const formData = new FormData();
    formData.append('agent_id', agentId);
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
    agentId: string
  ): Promise<SkillOperationResponse> {
    const params = new URLSearchParams({ agent_id: agentId });
    return this.request<SkillOperationResponse>(
      `/api/skills/${encodeURIComponent(skillName)}?${params}`,
      { method: 'DELETE' }
    );
  }

  async disableSkill(
    skillName: string,
    agentId: string
  ): Promise<SkillOperationResponse> {
    const params = new URLSearchParams({ agent_id: agentId });
    return this.request<SkillOperationResponse>(
      `/api/skills/${encodeURIComponent(skillName)}/disable?${params}`,
      { method: 'PUT' }
    );
  }

  async enableSkill(
    skillName: string,
    agentId: string
  ): Promise<SkillOperationResponse> {
    const params = new URLSearchParams({ agent_id: agentId });
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

  // Skill Study API — identity from X-User-Id / JWT headers.
  async studySkill(
    skillName: string,
    agentId: string
  ): Promise<SkillStudyResponse> {
    const params = new URLSearchParams({ agent_id: agentId });
    return this.request<SkillStudyResponse>(
      `/api/skills/${encodeURIComponent(skillName)}/study?${params}`,
      { method: 'POST' }
    );
  }

  async getSkillStudyStatus(
    skillName: string,
    agentId: string
  ): Promise<SkillStudyResponse> {
    const params = new URLSearchParams({ agent_id: agentId });
    return this.request<SkillStudyResponse>(
      `/api/skills/${encodeURIComponent(skillName)}/study?${params}`
    );
  }

  // Skill Env Config API — identity from X-User-Id / JWT headers.
  async getSkillEnvConfig(
    skillName: string,
    agentId: string
  ): Promise<SkillEnvConfigResponse> {
    const params = new URLSearchParams({ agent_id: agentId });
    return this.request<SkillEnvConfigResponse>(
      `/api/skills/${encodeURIComponent(skillName)}/env?${params}`
    );
  }

  async setSkillEnvConfig(
    skillName: string,
    agentId: string,
    envConfig: Record<string, string>
  ): Promise<SkillEnvConfigResponse> {
    const params = new URLSearchParams({ agent_id: agentId });
    return this.request<SkillEnvConfigResponse>(
      `/api/skills/${encodeURIComponent(skillName)}/env?${params}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ env_config: envConfig }),
      }
    );
  }
  /** Probe of /api/providers — returns the calling user's provider/slot
   * config. Identity is taken from the X-User-Id header that this client
   * attaches automatically; no query param is passed. Used by App.tsx
   * post-login to decide whether to send the user through Setup.
   *
   * Response is typed loosely (Record<string, any>) because the full
   * provider schema is only consumed inside ProviderSettings, and App
   * only needs `Object.keys(providers).length`. */
  async getProviders(): Promise<{
    success: boolean;
    data?: {
      providers: Record<string, unknown>;
      slots: Record<string, unknown>;
      version?: number;
    };
  }> {
    return this.request(`/api/providers`);
  }

  // Embedding Status API (per-user)
  async getEmbeddingStatus(userId: string): Promise<EmbeddingStatusResponse> {
    const qs = `?user_id=${encodeURIComponent(userId)}`;
    return this.request<EmbeddingStatusResponse>(`/api/providers/embeddings/status${qs}`);
  }

  async rebuildEmbeddings(userId: string): Promise<EmbeddingRebuildResponse> {
    const qs = `?user_id=${encodeURIComponent(userId)}`;
    return this.request<EmbeddingRebuildResponse>(
      `/api/providers/embeddings/rebuild${qs}`,
      { method: 'POST' },
    );
  }

  /** Backfill the latest default models from the catalog into existing providers.
   * Identity comes from the X-User-Id / JWT header — no query param. */
  async syncProviderDefaults(): Promise<{
    success: boolean;
    updates: Array<{
      provider_id: string;
      name: string;
      source: string;
      protocol: string;
      added: string[];
    }>;
    providers_updated: number;
    total_models_added: number;
  }> {
    return this.request(`/api/providers/sync-defaults`, { method: 'POST' });
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

  // ── v2.1: lazy-loaded detail endpoints + job mutations ────────────────

  async getAgentSparkline(agentId: string, hours = 24): Promise<{ success: boolean; buckets: number[]; hours: number }> {
    return this.request(`/api/dashboard/agents/${encodeURIComponent(agentId)}/sparkline?hours=${hours}`);
  }

  async getJobDetail(jobId: string): Promise<{ success: boolean; job: unknown }> {
    return this.request(`/api/dashboard/jobs/${encodeURIComponent(jobId)}`);
  }

  async getSessionDetail(sessionId: string, agentId: string): Promise<{ success: boolean; session: unknown }> {
    return this.request(
      `/api/dashboard/sessions/${encodeURIComponent(sessionId)}?agent_id=${encodeURIComponent(agentId)}`,
    );
  }

  async retryJob(jobId: string): Promise<{ success: boolean; job_id: string; new_status: string }> {
    return this.request(`/api/dashboard/jobs/${encodeURIComponent(jobId)}/retry`, { method: 'POST' });
  }

  async pauseJob(jobId: string): Promise<{ success: boolean; job_id: string; new_status: string }> {
    return this.request(`/api/dashboard/jobs/${encodeURIComponent(jobId)}/pause`, { method: 'POST' });
  }

  async resumeJob(jobId: string): Promise<{ success: boolean; job_id: string; new_status: string }> {
    return this.request(`/api/dashboard/jobs/${encodeURIComponent(jobId)}/resume`, { method: 'POST' });
  }

  // Lark / Feishu Integration API
  async getLarkCredential(agentId: string): Promise<LarkCredentialResponse> {
    return this.request<LarkCredentialResponse>(`/api/lark/credential?agent_id=${encodeURIComponent(agentId)}`);
  }

  async bindLarkBot(agentId: string, appId: string, appSecret: string, brand: string, ownerEmail: string = ''): Promise<LarkBindResponse> {
    return this.request<LarkBindResponse>('/api/lark/bind', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId, app_id: appId, app_secret: appSecret, brand, owner_email: ownerEmail }),
    });
  }

  async larkAuthLogin(agentId: string): Promise<LarkAuthLoginResponse> {
    return this.request<LarkAuthLoginResponse>('/api/lark/auth/login', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId }),
    });
  }

  async larkAuthComplete(agentId: string, deviceCode: string): Promise<LarkAuthCompleteResponse> {
    return this.request<LarkAuthCompleteResponse>('/api/lark/auth/complete', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId, device_code: deviceCode }),
    });
  }

  async getLarkAuthStatus(agentId: string): Promise<ApiResponse> {
    return this.request<ApiResponse>(`/api/lark/auth/status?agent_id=${encodeURIComponent(agentId)}`);
  }

  async testLarkConnection(agentId: string): Promise<ApiResponse> {
    return this.request<ApiResponse>('/api/lark/test', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId }),
    });
  }

  async unbindLarkBot(agentId: string): Promise<ApiResponse> {
    return this.request<ApiResponse>('/api/lark/unbind', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId }),
    });
  }

  // Slack Integration API
  async getSlackCredential(agentId: string): Promise<SlackCredentialResponse> {
    return this.request<SlackCredentialResponse>(`/api/slack/credential?agent_id=${encodeURIComponent(agentId)}`);
  }

  async bindSlackBot(
    agentId: string,
    botToken: string,
    appToken: string,
    ownerEmail: string = '',
  ): Promise<SlackBindResponse> {
    return this.request<SlackBindResponse>('/api/slack/bind', {
      method: 'POST',
      body: JSON.stringify({
        agent_id: agentId,
        bot_token: botToken,
        app_token: appToken,
        owner_email: ownerEmail,
      }),
    });
  }

  async testSlackConnection(agentId: string): Promise<SlackTestResponse> {
    return this.request<SlackTestResponse>('/api/slack/test', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId }),
    });
  }

  async unbindSlackBot(agentId: string): Promise<ApiResponse> {
    return this.request<ApiResponse>('/api/slack/unbind', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId }),
    });
  }

  // Telegram Integration API
  async getTelegramCredential(agentId: string): Promise<TelegramCredentialResponse> {
    return this.request<TelegramCredentialResponse>(`/api/telegram/credential?agent_id=${encodeURIComponent(agentId)}`);
  }

  async bindTelegramBot(
    agentId: string,
    botToken: string,
    ownerUsername: string = '',
  ): Promise<TelegramBindResponse> {
    return this.request<TelegramBindResponse>('/api/telegram/bind', {
      method: 'POST',
      body: JSON.stringify({
        agent_id: agentId,
        bot_token: botToken,
        owner_username: ownerUsername,
      }),
    });
  }

  async testTelegramConnection(agentId: string): Promise<TelegramTestResponse> {
    return this.request<TelegramTestResponse>('/api/telegram/test', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId }),
    });
  }

  async unbindTelegramBot(agentId: string): Promise<ApiResponse> {
    return this.request<ApiResponse>('/api/telegram/unbind', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId }),
    });
  }

  // System-default free-tier quota
  async getMyQuota(): Promise<QuotaMeResponse> {
    return this.request<QuotaMeResponse>('/api/quota/me');
  }

  async setQuotaPreference(preferSystemOverride: boolean): Promise<QuotaMeResponse> {
    return this.request<QuotaMeResponse>('/api/quota/me/preference', {
      method: 'PATCH',
      body: JSON.stringify({ prefer_system_override: preferSystemOverride }),
    });
  }

  // =========================================================================
  // Subproject 1: Teams
  // =========================================================================

  async listTeams(): Promise<TeamListResponse> {
    return this.request<TeamListResponse>('/api/teams');
  }

  async createTeam(payload: { name: string; description?: string; color?: string }): Promise<TeamOperationResponse> {
    return this.request<TeamOperationResponse>('/api/teams', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async updateTeam(teamId: string, patch: { name?: string; description?: string; color?: string; intro_md?: string }): Promise<TeamOperationResponse> {
    return this.request<TeamOperationResponse>(`/api/teams/${encodeURIComponent(teamId)}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    });
  }

  async deleteTeam(teamId: string): Promise<TeamOperationResponse> {
    return this.request<TeamOperationResponse>(`/api/teams/${encodeURIComponent(teamId)}`, {
      method: 'DELETE',
    });
  }

  async addTeamMember(teamId: string, agentId: string): Promise<TeamOperationResponse> {
    return this.request<TeamOperationResponse>(`/api/teams/${encodeURIComponent(teamId)}/members`, {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId }),
    });
  }

  async removeTeamMember(teamId: string, agentId: string): Promise<TeamOperationResponse> {
    return this.request<TeamOperationResponse>(
      `/api/teams/${encodeURIComponent(teamId)}/members/${encodeURIComponent(agentId)}`,
      { method: 'DELETE' }
    );
  }

  // =========================================================================
  // Subproject 2: Bundle export/import
  // =========================================================================

  async exportBundle(payload: BundleExportRequest): Promise<{ blob: Blob; filename: string; warningsCount: number; externalEdgesDropped: number }> {
    const baseUrl = getApiBaseUrl();
    const authHeaders = this.getAuthHeaders();
    const resp = await fetch(`${baseUrl}/api/bundle/export`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      // Try to parse a structured error so callers can act on it (B6 sensitive zip flow)
      let detail: any = null;
      try { detail = (await resp.json()).detail; } catch {}
      const err: any = new Error(
        detail?.message || `Export failed: ${resp.status}`
      );
      if (detail?.error_code) err.code = detail.error_code;
      if (detail?.hits) err.hits = detail.hits;
      err.status = resp.status;
      throw err;
    }
    const cd = resp.headers.get('Content-Disposition') || '';
    const m = /filename="([^"]+)"/.exec(cd);
    const filename = m ? m[1] : `bundle-${Date.now()}.nxbundle`;
    const warningsCount = parseInt(resp.headers.get('X-Bundle-Warnings-Count') || '0');
    const externalEdgesDropped = parseInt(resp.headers.get('X-Bundle-External-Edges-Dropped') || '0');
    const blob = await resp.blob();
    return { blob, filename, warningsCount, externalEdgesDropped };
  }

  async importBundlePreflight(file: File): Promise<BundlePreflightResponse> {
    const baseUrl = getApiBaseUrl();
    const authHeaders = this.getAuthHeaders();
    const fd = new FormData();
    fd.append('file', file);
    const resp = await fetch(`${baseUrl}/api/bundle/import/preflight`, {
      method: 'POST',
      headers: { ...authHeaders },
      body: fd,
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(text || `Preflight failed: ${resp.status}`);
    }
    return resp.json();
  }

  async importBundleConfirm(preflightToken: string): Promise<BundleConfirmResponse> {
    return this.request<BundleConfirmResponse>('/api/bundle/import/confirm', {
      method: 'POST',
      body: JSON.stringify({ preflight_token: preflightToken }),
    });
  }

  /** Deep-link install path: backend fetches the URL itself then preflights.
   *  Used by the /app/templates/install page when called from narra.nexus. */
  async importBundleFromUrl(
    url: string,
    expectedSha256?: string,
  ): Promise<BundlePreflightResponse> {
    return this.request<BundlePreflightResponse>('/api/bundle/import/from-url', {
      method: 'POST',
      body: JSON.stringify({
        url,
        expected_sha256: expectedSha256 ?? null,
      }),
    });
  }

  async previewBusChannels(agentIds: string[]): Promise<{ channels: Array<{
    channel_id: string;
    name: string;
    channel_type: string;
    in_closure_member_ids: string[];
    all_member_ids: string[];
    message_count: number;
    created_at?: string | null;
  }> }> {
    return this.request('/api/bundle/export/preview/bus-channels', {
      method: 'POST',
      body: JSON.stringify({ agent_ids: agentIds }),
    });
  }

  async previewArtifacts(agentIds: string[]): Promise<{
    agents: Record<string, BundleArtifactPreview[]>;
  }> {
    return this.request('/api/bundle/export/preview/artifacts', {
      method: 'POST',
      body: JSON.stringify({ agent_ids: agentIds }),
    });
  }

  async previewMcps(agentIds: string[]): Promise<{
    agents: Record<string, BundleMcpPreview[]>;
  }> {
    return this.request('/api/bundle/export/preview/mcps', {
      method: 'POST',
      body: JSON.stringify({ agent_ids: agentIds }),
    });
  }

  async listSkillArchives(): Promise<{ archives: SkillArchiveRecord[] }> {
    return this.request<{ archives: SkillArchiveRecord[] }>('/api/bundle/skills/archives');
  }

  async uploadSkillArchive(payload: { skillName: string; sourceType: 'github' | 'zip'; sourceUrl?: string; file?: File }): Promise<{ success: boolean; skill_name: string }> {
    const baseUrl = getApiBaseUrl();
    const authHeaders = this.getAuthHeaders();
    const fd = new FormData();
    fd.append('skill_name', payload.skillName);
    fd.append('source_type', payload.sourceType);
    if (payload.sourceUrl) fd.append('source_url', payload.sourceUrl);
    if (payload.file) fd.append('file', payload.file);
    const resp = await fetch(`${baseUrl}/api/bundle/skills/archives/upload`, {
      method: 'POST',
      headers: { ...authHeaders },
      body: fd,
    });
    if (!resp.ok) throw new Error(`Upload archive failed: ${resp.status}`);
    return resp.json();
  }
}

// ─────────────────────────────────────────────────────────────────────────
// Mock layer — when enabled (?mock=1 or localStorage), calls fall through
// to hand-authored fixtures instead of the backend. The real ApiClient
// instance is preserved and used for any method the mock doesn't override
// so the UI never 404s in mock mode. See src/lib/mock/index.ts.
// ─────────────────────────────────────────────────────────────────────────
import { MOCK_ENABLED, mockApi } from './mock';

const _realApi = new ApiClient();

export const api: ApiClient = MOCK_ENABLED
  ? (() => {
      // eslint-disable-next-line no-console
      console.info(
        '%c[MOCK]',
        'background:#111214;color:#fff;padding:2px 6px;border-radius:0;',
        'API mock layer active. Toggle off with ?mock=0 or the dev banner.'
      );
      return new Proxy(_realApi, {
        get(target, prop, receiver) {
          const mocked = (mockApi as unknown as Record<string | symbol, unknown>)[prop];
          if (typeof mocked === 'function') return mocked.bind(mockApi);
          return Reflect.get(target, prop, receiver);
        },
      });
    })()
  : _realApi;
