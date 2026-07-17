/**
 * API client for backend communication
 * Uses relative paths in dev (Vite proxy) and configurable base URL in production
 */

import type {
  JobListResponse,
  JobDetailResponse,
  CancelJobResponse,
  AgentInboxListResponse,
  BusFailuresResponse,
  AgentCircuitBreakerResponse,
  MarkReadResponse,
  NoticesResponse,
  AwarenessResponse,
  ClearHistoryResponse,
  SocialNetworkResponse,
  SocialNetworkListResponse,
  MyNarrativesResponse,
  MyNetworkResponse,
  MyWorldviewResponse,
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
  CreateJobComplexRequest,
  CreateJobComplexResponse,
  LoginResponse,
  NetmindLoginResponse,
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
  DashboardResponse,
  ApiResponse,
  LarkCredentialResponse,
  LarkBindResponse,
  LarkAuthLoginResponse,
  LarkAuthCompleteResponse,
  TeamListResponse,
  TeamOperationResponse,
  TeamChatHistoryResponse,
  TeamChatSendResponse,
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
  NarramessengerCredentialResponse,
  NarramessengerBindResponse,
  TelegramTestResponse,
  WeChatCredentialResponse,
  WeChatQrStartResponse,
  WeChatQrPollResponse,
  DiscordCredentialResponse,
  DiscordBindResponse,
  DiscordTestResponse,
  PlanListResponse,
  SubscriptionMeResponse,
  SubscribeResponse,
  BillingActionResponse,
  FeeInfoResponse,
  RecordsResponse,
  RechargeResponse,
  RechargeStatusResponse,
  AgentSlotView,
  AgentSlotEffective,
} from '@/types';

// Base URL resolution is delegated to runtimeStore.getApiBaseUrl() so
// every request picks up the CURRENT mode/cloudApiUrl. See runtimeStore.ts
// for resolution order. This export is kept for backwards compatibility.
export { getApiBaseUrl as getBaseUrl } from '@/stores/runtimeStore';
import { getApiBaseUrl } from '@/stores/runtimeStore';

/** Sources accepted by POST /api/providers/onboard (one-key setup). */
export type OnboardProviderType =
  | 'anthropic'
  | 'openai'
  | 'netmind'
  | 'yunwu'
  | 'openrouter';

class ApiClient {
  // Public so download helpers (lib/download.ts) can attach the same
  // identity headers to direct fetch / Tauri-proxy file requests that
  // <a download> elements cannot carry.
  getAuthHeaders(): Record<string, string> {
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
        // Billing routes authenticate the NetMind loginToken (X-Netmind-Token),
        // NOT the NarraNexus session JWT. A 401 here means the NetMind token is
        // missing/expired — it must NOT log the user out of their valid
        // NarraNexus session. The panel handles this failure locally.
        const isBillingEndpoint = endpoint.startsWith('/api/billing/');
        if (!isAuthEndpoint && !isBillingEndpoint) {
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

  /**
   * Mark every message in a channel as read (advance `last_read_at` to
   * NOW server-side). Used by the "click the channel row to clear its
   * unread badge" UX — 2026-05-28.
   *
   * Why not loop over `markAgentMessageRead`: the inbox response caps
   * messages per channel at 50 but `unread_count` is computed against
   * ALL messages, so marking the latest VISIBLE message can leave an
   * unread tail. The room-level endpoint advances the cursor to NOW
   * directly, guaranteeing zero residual unread.
   */
  async markAgentRoomRead(roomId: string, agentId: string): Promise<MarkReadResponse> {
    return this.request<MarkReadResponse>(
      `/api/agent-inbox/rooms/${encodeURIComponent(roomId)}/read?agent_id=${encodeURIComponent(agentId)}`,
      { method: 'POST' }
    );
  }

  // Bus-failure recovery (upstream #52) — messages the agent gave up on.
  async getBusFailures(agentId: string): Promise<BusFailuresResponse> {
    return this.request<BusFailuresResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/bus-failures`
    );
  }

  /** Clear one parked failure so the next bus poll re-delivers it. */
  async retryBusFailure(agentId: string, messageId: string): Promise<ApiResponse> {
    return this.request<ApiResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/bus-failures/${encodeURIComponent(messageId)}/retry`,
      { method: 'POST' }
    );
  }

  // Real-time-layer circuit-breaker: read status + manual "Resume".
  async getAgentCircuitBreaker(agentId: string): Promise<AgentCircuitBreakerResponse> {
    return this.request<AgentCircuitBreakerResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/circuit-breaker`
    );
  }

  /** Manually clear a paused agent back to active so its next turn runs. */
  async resetAgentCircuitBreaker(agentId: string): Promise<ApiResponse> {
    return this.request<ApiResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/circuit-breaker/reset`,
      { method: 'POST' }
    );
  }

  // User-scope system notices (inbox_table read side).
  async getNotices(unreadOnly = false, limit = 50): Promise<NoticesResponse> {
    return this.request<NoticesResponse>(
      `/api/notices?unread_only=${unreadOnly}&limit=${limit}`
    );
  }

  async markNoticeRead(messageId: string): Promise<ApiResponse> {
    return this.request<ApiResponse>(
      `/api/notices/${encodeURIComponent(messageId)}/read`,
      { method: 'POST' }
    );
  }

  /** Send user-written product feedback to the team (relayed server-side). */
  async submitFeedback(category: string, text: string): Promise<{ ok: boolean; delivered: boolean }> {
    return this.request<{ ok: boolean; delivered: boolean }>('/api/feedback', {
      method: 'POST',
      body: JSON.stringify({ category, text }),
    });
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

  /** Owner-scoped: every narrative across all the user's agents, for the
   *  "You" workspace Narra Memory timeline. Seeded scaffold narratives are
   *  excluded unless includeDefault is set. */
  async getMyNarratives(includeDefault = false): Promise<MyNarrativesResponse> {
    const qs = includeDefault ? '?include_default=true' : '';
    return this.request<MyNarrativesResponse>(`/api/me/narratives${qs}`);
  }

  /** Owner-scoped: every entity the user's agents know, merged across agents,
   *  for the "You" workspace Nexus Network graph. */
  async getMyNetwork(): Promise<MyNetworkResponse> {
    return this.request<MyNetworkResponse>('/api/me/network');
  }

  /** Owner-scoped: how each of the user's agents sees them + each agent's own
   *  worldview, for the "You" workspace Worldview tab. */
  async getMyWorldview(): Promise<MyWorldviewResponse> {
    return this.request<MyWorldviewResponse>('/api/me/worldview');
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

  async clearHistory(
    agentId: string,
    opts: { conversations: boolean; memory: boolean } = { conversations: true, memory: true },
  ): Promise<ClearHistoryResponse> {
    const params = new URLSearchParams({
      conversations: String(opts.conversations),
      memory: String(opts.memory),
    });
    return this.request<ClearHistoryResponse>(
      `/api/agents/${encodeURIComponent(agentId)}/history?${params}`,
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

  async netmindLogin(netmindToken: string, source?: string): Promise<NetmindLoginResponse> {
    return this.request<NetmindLoginResponse>('/api/auth/netmind-login', {
      method: 'POST',
      body: JSON.stringify({ netmind_token: netmindToken, source: source || undefined }),
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

  /** Get the current user's analytics opt-out preference. Identity travels
   *  in the auth header; the server derives the user from it. Returns false
   *  when no row exists (opted in by default). */
  async getAnalyticsOptOut(): Promise<boolean> {
    const r = await this.request<{ opted_out: boolean }>(
      '/api/auth/settings/analytics',
    );
    return Boolean(r.opted_out);
  }

  /** Set the current user's analytics opt-out preference. */
  async setAnalyticsOptOut(optedOut: boolean): Promise<void> {
    await this.request<{ success: boolean; opted_out: boolean }>(
      '/api/auth/settings/analytics',
      {
        method: 'PUT',
        body: JSON.stringify({ opted_out: optedOut }),
      },
    );
  }

  /** Report a frontend funnel event (setup page UI actions). Identity comes
   *  from the auth header server-side; no client properties are accepted
   *  (the server stamps surface etc. itself). Best-effort: callers should
   *  not block on it (fire-and-forget with a .catch). */
  async trackFunnelEvent(event: string): Promise<void> {
    await this.request<{ success: boolean }>('/api/auth/funnel', {
      method: 'POST',
      body: JSON.stringify({ event }),
    });
  }

  async getAgents(): Promise<AgentListResponse> {
    return this.request<AgentListResponse>(`/api/auth/agents`);
  }

  // Arena onboarding: ensure the authenticated user has a provisioned Arena
  // agent and return it. Idempotent server-side (one Arena agent per user).
  // The user identity comes from the session; the optional `userToken` is the
  // user's NetMind JWT, forwarded so the backend can bind the agent's owner
  // email via Arena's platform-only endpoint (no email round-trip). It is sent
  // to Arena and never persisted. See backend/routes/arena.py.
  async provisionArena(userToken?: string): Promise<{
    success: boolean;
    reused?: boolean;
    status?: string;
    agent_id?: string;
    arena_agent_id?: string;
    arena_name?: string;
    owner_bind?: string;
  }> {
    return this.request('/api/arena/provision', {
      method: 'POST',
      body: userToken ? JSON.stringify({ user_token: userToken }) : undefined,
    });
  }

  async createAgent(
    createdBy: string,
    agentName?: string,
    agentDescription?: string,
    opts?: { teamId?: string },
  ): Promise<CreateAgentResponse> {
    return this.request<CreateAgentResponse>('/api/auth/agents', {
      method: 'POST',
      body: JSON.stringify({
        created_by: createdBy,
        agent_name: agentName,
        agent_description: agentDescription,
        // #43: attach to the team the user clicked "Add agent" under.
        team_id: opts?.teamId,
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

  /** One-key onboarding: a single API key wires the agent framework,
   * the provider, and both slots (agent + helper_llm) in one call.
   * providerType overrides the backend's sk-ant- prefix detection;
   * aggregator keys (netmind/yunwu/openrouter) MUST pass it — they
   * have no recognisable prefix. */
  async onboard(
    apiKey: string,
    providerType?: OnboardProviderType,
    replace?: boolean,
  ): Promise<{
    success: boolean;
    detail?: string;
    provider_ids?: string[];
    provider_type?: string;
    agent_framework?: string;
    agent_model?: string;
    helper_model?: string;
    /** "ok" | "unverified (<reason>)" — live key probe result. A
     * definitively bad key never reaches success (400 instead). */
    key_check?: string;
    /** Set when the user already has a provider of this (aggregator) type.
     * The UI confirms and re-sends with replace=true to rotate the key. */
    needs_replace?: boolean;
    /** Masked tail of the currently-configured key, e.g. "***fXQA". */
    existing_masked?: string;
  }> {
    return this.request(`/api/providers/onboard`, {
      method: 'POST',
      body: JSON.stringify({
        api_key: apiKey,
        provider_type: providerType ?? null,
        replace: replace ?? false,
      }),
    });
  }

  /** Update one provider slot (e.g. 'agent' / 'helper_llm') — the model the
   *  user's agents use for that role. Same endpoint Settings › Providers uses;
   *  surfaced in the composer so the model can be switched without leaving chat. */
  async setProviderSlot(
    slot: string,
    body: { provider_id: string; model: string; thinking?: string; reasoning_effort?: string },
  ): Promise<{ success: boolean; detail?: string }> {
    return this.request(`/api/providers/slots/${slot}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
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

  /** Get the user's coding-agent framework choice + auth probe. */
  async getAgentFramework(): Promise<{
    success: boolean;
    data: {
      framework: string;
      supported: string[];
      probe: { ok: boolean; detail: string };
    };
  }> {
    return this.request(`/api/providers/agent-framework`);
  }

  /** Set the user's coding-agent framework choice.
   *
   * For ``framework === 'codex_cli'``, the backend may auto-install
   * ``@openai/codex`` via ``npm install -g`` in local mode. The
   * ``install`` field reports the outcome:
   *   - ``null``                   — codex install not attempted (claude_code)
   *   - ``{action: 'already_installed'}`` — binary was already on PATH
   *   - ``{action: 'auto_installed'}``    — we just installed it
   *   - ``{action: 'blocked'}``           — cloud mode: install refused
   *   - ``{action: 'install_failed'}``    — see ``reason``
   */
  async setAgentFramework(framework: string): Promise<{
    success: boolean;
    data: {
      framework: string;
      probe: { ok: boolean; detail: string };
      install: {
        installed: boolean;
        action: 'already_installed' | 'auto_installed' | 'blocked' | 'install_failed';
        reason: string;
      } | null;
    };
  }> {
    return this.request(`/api/providers/agent-framework`, {
      method: 'POST',
      body: JSON.stringify({ framework }),
    });
  }

  // ── per-agent LLM config overrides ────────────────────────────────────
  // An agent inherits the owner's user-level slots by default; these override
  // just that agent's agent/helper slots. See backend agents_llm_config.py.

  /** Per-slot view: inheriting/effective/override/owner_default for the
   *  agent's 'agent' and 'helper_llm' slots. */
  async getAgentLlmConfig(agentId: string): Promise<{
    success: boolean;
    data?: {
      agent_id: string;
      slots: Record<string, AgentSlotView>;
    };
  }> {
    return this.request(`/api/agents/${encodeURIComponent(agentId)}/llm-config`);
  }

  /** Set (or replace) this agent's override for one slot. */
  async setAgentLlmConfig(
    agentId: string,
    slot: string,
    body: {
      provider_id: string;
      model: string;
      thinking?: string;
      reasoning_effort?: string;
      agent_framework?: string | null;
    },
  ): Promise<{ success: boolean; detail?: string; data?: { slot: AgentSlotEffective | null } }> {
    return this.request(
      `/api/agents/${encodeURIComponent(agentId)}/llm-config/${slot}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    );
  }

  /** Reset a slot to inherit the owner default. slot='all' resets both. */
  async resetAgentLlmConfig(
    agentId: string,
    slot: string,
  ): Promise<{ success: boolean; detail?: string }> {
    return this.request(
      `/api/agents/${encodeURIComponent(agentId)}/llm-config/${slot}`,
      { method: 'DELETE' },
    );
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

  // Activate/deactivate a bound channel credential (flip is_active/enabled)
  // without re-binding. Used to turn a bundle-imported (inactive) channel live.
  async setLarkActive(agentId: string, active: boolean): Promise<ApiResponse> {
    return this.request<ApiResponse>('/api/lark/set-active', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId, active }),
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

  async setSlackActive(agentId: string, active: boolean): Promise<ApiResponse> {
    return this.request<ApiResponse>('/api/slack/set-active', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId, active }),
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

  async setTelegramActive(agentId: string, active: boolean): Promise<ApiResponse> {
    return this.request<ApiResponse>('/api/telegram/set-active', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId, active }),
    });
  }

  // WeChat (iLink) Integration API — QR-scan bind flow (no token paste).
  async getWeChatCredential(agentId: string): Promise<WeChatCredentialResponse> {
    return this.request<WeChatCredentialResponse>(`/api/wechat/credential?agent_id=${encodeURIComponent(agentId)}`);
  }

  async startWeChatQrcode(agentId: string): Promise<WeChatQrStartResponse> {
    return this.request<WeChatQrStartResponse>('/api/wechat/qrcode/start', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId }),
    });
  }

  async pollWeChatQrcode(
    agentId: string,
    qrcode: string,
  ): Promise<WeChatQrPollResponse> {
    // No base_url: the gateway host is fixed server-side (a client-supplied
    // host would be an SSRF vector). The backend ignores any extra field.
    return this.request<WeChatQrPollResponse>('/api/wechat/qrcode/poll', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId, qrcode }),
    });
  }

  async unbindWeChat(agentId: string): Promise<ApiResponse> {
    return this.request<ApiResponse>('/api/wechat/unbind', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId }),
    });
  }

  async setWeChatActive(agentId: string, active: boolean): Promise<ApiResponse> {
    return this.request<ApiResponse>('/api/wechat/set-active', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId, active }),
    });
  }

  async getNarramessengerCredential(agentId: string): Promise<NarramessengerCredentialResponse> {
    return this.request<NarramessengerCredentialResponse>(`/api/narramessenger/credential?agent_id=${encodeURIComponent(agentId)}`);
  }

  async bindNarramessenger(agentId: string, bindCommand: string): Promise<NarramessengerBindResponse> {
    return this.request<NarramessengerBindResponse>('/api/narramessenger/bind', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId, bind_command: bindCommand }),
    });
  }

  async unbindNarramessenger(agentId: string): Promise<ApiResponse> {
    return this.request<ApiResponse>('/api/narramessenger/unbind', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId }),
    });
  }

  // Discord Integration API
  async getDiscordCredential(agentId: string): Promise<DiscordCredentialResponse> {
    return this.request<DiscordCredentialResponse>(`/api/discord/credential?agent_id=${encodeURIComponent(agentId)}`);
  }

  async bindDiscordBot(
    agentId: string,
    botToken: string,
    ownerUserId: string = '',
  ): Promise<DiscordBindResponse> {
    return this.request<DiscordBindResponse>('/api/discord/bind', {
      method: 'POST',
      body: JSON.stringify({
        agent_id: agentId,
        bot_token: botToken,
        owner_user_id: ownerUserId,
      }),
    });
  }

  async testDiscordConnection(agentId: string): Promise<DiscordTestResponse> {
    return this.request<DiscordTestResponse>('/api/discord/test', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId }),
    });
  }

  async unbindDiscordBot(agentId: string): Promise<ApiResponse> {
    return this.request<ApiResponse>('/api/discord/unbind', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId }),
    });
  }

  async setDiscordActive(agentId: string, active: boolean): Promise<ApiResponse> {
    return this.request<ApiResponse>('/api/discord/set-active', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId, active }),
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
  // NetMind billing / subscription (Phase 1)
  // The user's NetMind loginToken lives in configStore.netmindToken and is
  // forwarded per-request via X-Netmind-Token (backend never stores it).
  // =========================================================================

  private getNetmindToken(): string {
    try {
      const raw = localStorage.getItem('narra-nexus-config');
      if (raw) return JSON.parse(raw)?.state?.netmindToken || '';
    } catch {
      /* localStorage unavailable — fall through to empty */
    }
    return '';
  }

  async getPlans(): Promise<PlanListResponse> {
    return this.request<PlanListResponse>('/api/billing/plans');
  }

  async getSubscription(): Promise<SubscriptionMeResponse> {
    const token = this.getNetmindToken();
    if (!token) {
      // Fail fast client-side: no NetMind loginToken means the account isn't
      // linked (or the session predates the ?token= bootstrap). Don't send an
      // empty header round-trip; let the panel show its error/link state.
      throw new Error('NetMind account not linked (no loginToken)');
    }
    return this.request<SubscriptionMeResponse>('/api/billing/subscription', {
      headers: { 'X-Netmind-Token': token },
    });
  }

  async getFeeInfo(): Promise<FeeInfoResponse> {
    const token = this.getNetmindToken();
    if (!token) throw new Error('NetMind account not linked (no loginToken)');
    return this.request<FeeInfoResponse>('/api/billing/fee-info', {
      headers: { 'X-Netmind-Token': token },
    });
  }

  // Recent financial records (consumption + recharge history). Optional
  // direction filter: 'expense' | 'income'.
  async getRecords(direction?: 'expense' | 'income'): Promise<RecordsResponse> {
    const token = this.getNetmindToken();
    if (!token) throw new Error('NetMind account not linked (no loginToken)');
    const qs = direction ? `?direction=${direction}` : '';
    return this.request<RecordsResponse>(`/api/billing/records${qs}`, {
      headers: { 'X-Netmind-Token': token },
    });
  }

  // Module F: one-click "use my NetMind subscription" — backend mints an
  // inference key and wires it to the agent/helper slots. POST + loginToken.
  async useSubscription(): Promise<{ success: boolean; provider_ids?: string[] }> {
    const token = this.getNetmindToken();
    if (!token) throw new Error('NetMind account not linked (no loginToken)');
    return this.request<{ success: boolean; provider_ids?: string[] }>(
      '/api/providers/use-subscription',
      { method: 'POST', headers: { 'X-Netmind-Token': token } },
    );
  }

  private billingWrite<T>(endpoint: string): Promise<T> {
    const token = this.getNetmindToken();
    if (!token) throw new Error('NetMind account not linked (no loginToken)');
    return this.request<T>(endpoint, {
      method: 'POST',
      headers: { 'X-Netmind-Token': token },
    });
  }

  // Start a Pro subscription — returns Stripe checkout_url to redirect to.
  async subscribe(): Promise<SubscribeResponse> {
    return this.billingWrite<SubscribeResponse>('/api/billing/subscribe');
  }

  // Cancel = turn off auto-renew (stays Pro until period end).
  async cancelSubscription(): Promise<BillingActionResponse> {
    return this.billingWrite<BillingActionResponse>('/api/billing/cancel');
  }

  // Re-enable auto-renew on a cancelled-but-in-period subscription.
  async reactivateSubscription(): Promise<BillingActionResponse> {
    return this.billingWrite<BillingActionResponse>('/api/billing/reactivate');
  }

  // Module E: top-up. Create a hosted Stripe checkout for `amount` (USD by
  // default) and return checkout_url to open externally, then poll
  // rechargeStatus(session_id) until succeeded/failed.
  async recharge(amount: number, currency = 'USD'): Promise<RechargeResponse> {
    const token = this.getNetmindToken();
    if (!token) throw new Error('NetMind account not linked (no loginToken)');
    return this.request<RechargeResponse>('/api/billing/recharge', {
      method: 'POST',
      headers: { 'X-Netmind-Token': token },
      body: JSON.stringify({ amount, currency }),
    });
  }

  // Poll a recharge by its Stripe session id -> { status: pending|succeeded|failed }.
  async rechargeStatus(sessionId: string): Promise<RechargeStatusResponse> {
    const token = this.getNetmindToken();
    if (!token) throw new Error('NetMind account not linked (no loginToken)');
    return this.request<RechargeStatusResponse>(
      `/api/billing/recharge/${encodeURIComponent(sessionId)}`,
      { headers: { 'X-Netmind-Token': token } },
    );
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

  // --- Team group chat (over the message bus) ---

  async getTeamChat(teamId: string, since?: string): Promise<TeamChatHistoryResponse> {
    const q = since ? `?since=${encodeURIComponent(since)}` : '';
    return this.request<TeamChatHistoryResponse>(
      `/api/teams/${encodeURIComponent(teamId)}/chat/messages${q}`,
    );
  }

  /** Post a user message into a team's group chat. `mentions` carries
   *  agent_ids and/or the literal "@all" (the backend maps it to @everyone). */
  async sendTeamChat(teamId: string, content: string, mentions: string[]): Promise<TeamChatSendResponse> {
    return this.request<TeamChatSendResponse>(
      `/api/teams/${encodeURIComponent(teamId)}/chat/messages`,
      { method: 'POST', body: JSON.stringify({ content, mentions }) },
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
