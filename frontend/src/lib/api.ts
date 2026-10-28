/**
 * API client for backend communication
 * Uses relative paths in dev (Vite proxy) and configurable base URL in production
 */

import type { Artifact, TeamFile } from '@/types/artifact';
import { MARKETPLACE_SEARCH_TIMEOUT_MS } from '@/lib/apiTimeouts';
import type {
  AgentCapabilitiesView,
  ChannelCredentialView,
  ChannelSchema,
  PluginChannelRow,
  MigrationFramework,
  MigrationDetectResponse,
  StandardizedAgentImport,
  MigrationApplyResult,
  BusAttachment,
  JobListResponse,
  JobDetailResponse,
  CancelJobResponse,
  AgentInboxListResponse,
  BusFailuresResponse,
  AgentCircuitBreakerResponse,
  MarkReadResponse,
  NoticesResponse,
  AwarenessResponse,
  CancelRunResponse,
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
  QuotaMeResponse,
  AgentListResponse,
  SessionResponse,
  CreateUserResponse,
  UpdateTimezoneResponse,
  OnboardingResponse,
  SkillListResponse,
  SkillOperationResponse,
  SkillStudyResponse,
  MarketplaceSearchResponse,
  MarketplaceSkillDetail,
  MarketplaceInstallResponse,
  SkillUpdateInfo,
  CostResponse,
  SkillEnvConfigResponse,
  DashboardResponse,
  ApiResponse,
  LarkAuthLoginResponse,
  LarkAuthCompleteResponse,
  TeamListResponse,
  TeamOperationResponse,
  TeamChatHistoryResponse,
  TeamChatSendResponse,
  TeamBulletin,
  BulletinEntry,
  TeamWorkBoardResponse,
  BundleExportRequest,
  BundlePreflightResponse,
  TeamTemplate,
  TeamMarketplaceListResponse,
  BundleConfirmResponse,
  BundleArtifactPreview,
  BundleMcpPreview,
  SkillArchiveRecord,
  WeChatQrStartResponse,
  WeChatQrPollResponse,
  PlanListResponse,
  SubscriptionMeResponse,
  SubscribeResponse,
  BillingActionResponse,
  FeeInfoResponse,
  RecordsResponse,
  RechargeResponse,
  RechargePaymentMethod,
  SubscribePaymentMethod,
  FxQuoteResponse,
  RechargeStatusResponse,
  AgentSlotView,
  AgentSlotEffective,
  SlotOverrideStats,
  WorkerStatus,
  WorkerLiveness,
  PluginId,
  PluginsListResponse,
  PluginUninstallResponse,
  PluginInstallEvent,
  FactoryListResponse,
  FactoryInstallResponse,
  FactoryErrorsResponse,
  FactoryIndexResponse,
  FactoryProposalsResponse,
} from '@/types';

// Base URL resolution is delegated to runtimeStore.getApiBaseUrl() so
// every request picks up the CURRENT mode/cloudApiUrl. See runtimeStore.ts
// for resolution order. This export is kept for backwards compatibility.
export { getApiBaseUrl as getBaseUrl } from '@/stores/runtimeStore';
import { getApiBaseUrl } from '@/stores/runtimeStore';
import type { CliStatusPayload, ProviderRow } from './providersApi';
import { getAuthHeaders as readAuthHeaders } from './authHeaders';
import type {
  BrowserInstallResult,
  BrowserRuntimeStatus,
  BrowserPolicyCapability,
  BrowserPolicyRule,
  BrowserPolicyView,
  BrowserLoginRequest,
} from '@/types/browser';
import { markGuideCoachmarkPending } from './guideCoachmark';
import { isSessionDeadFailure, readAuthCode } from './authFailure';
import { confirmSessionDeath } from './sessionGuard';

/**
 * Error thrown for non-2xx API responses. Carries the HTTP status so
 * callers can branch on it (e.g. treat DELETE 404 as already-gone)
 * instead of string-matching the message.
 */
/** How long one patrol-switch PUT may stay in flight before the store gives
 *  up on it (see teamsStore.patrolInFlight). */
export const PATROL_WRITE_TIMEOUT_MS = 15_000;

export class ApiError extends Error {
  readonly status: number;
  /** The backend's raw HTTPException `detail` ('' when the body had
   * none) — callers that show user copy want this, not the prefixed
   * message. */
  readonly detail: string;

  constructor(status: number, message: string, detail: string = '') {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

export interface TelemetryConsentState {
  mode: 'off' | 'meta' | 'full';
  source: 'env' | 'optout' | 'default';
  opted_out: boolean;
  controllable: boolean;
  /** Who owns a non-controllable state: deployment env var vs
   *  multi-tenant cloud install. null when the user holds the switch. */
  managed_by: 'env' | 'cloud' | null;
}

/** Sources accepted by POST /api/providers/onboard (one-key setup). */
export type OnboardProviderType =
  | 'anthropic'
  | 'openai'
  | 'netmind'
  | 'yunwu'
  | 'openrouter';

/**
 * The auth-funnel stages the frontend may report. This union is a COMPILE-TIME
 * mirror of the backend allowlist `_FUNNEL_STAGES` (backend/routes/auth.py):
 * the endpoint 400s any stage not in that set and reportAuthFunnel swallows the
 * 400, so an un-allowlisted stage reports into a black hole. Keeping this a
 * closed union means adding a new report site fails `tsc` until the name is
 * added HERE — forcing the author to the backend allowlist at the same time.
 */
export type AuthFunnelStage =
  | 'netmind_email_login_failed'
  | 'netmind_oauth_failed'
  | 'signup_send_code_failed'
  | 'netmind_reset_code_failed'
  | 'netmind_reset_password_failed';

class ApiClient {
  // Public so download helpers (lib/download.ts) can attach the same
  // identity headers to direct fetch / Tauri-proxy file requests that
  // <a download> elements cannot carry.
  getAuthHeaders(): Record<string, string> {
    // Implementation lives in lib/authHeaders so the session guard can
    // build the same headers without importing this module (cycle).
    // The single ApiClient stays mode-agnostic: the cloud/local decision
    // happens server-side in auth_middleware, not here.
    return readAuthHeaders();
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
      // Parse the error body once: it feeds both the thrown message and
      // (on 401) the session-death decision below.
      let body: unknown = null;
      try {
        body = await response.json();
      } catch {
        /* not JSON — fall through to status line */
      }

      // A 401 does NOT mean "your session is dead". It can equally mean a
      // second credential failed (NetMind login token, gateway token) or
      // that a handler failed to resolve an identity the middleware had
      // already verified. Only the backend's `code` can tell those apart,
      // and even then we confirm against GET /api/auth/session before
      // tearing anything down — see lib/sessionGuard.
      //
      // This replaced a denylist ("log out on any 401 except /api/auth/*
      // and /api/billing/*") whose every missing entry was a live grenade:
      // /api/providers' NetMind 401 was one, and it bounced demo users to
      // /login on 2026-08-02 while their sessions were perfectly valid.
      if (response.status === 401 && isSessionDeadFailure(body)) {
        void confirmSessionDeath({ endpoint, code: readAuthCode(body) });
      }

      // Extract the FastAPI HTTPException `detail` field so callers get
      // an actionable message ("Cannot add another user's agent") instead
      // of just "API error: 403 Forbidden". Falls back to the status
      // line if the body is missing / not JSON / has no `detail`.
      let detail = '';
      const rawDetail = (body as { detail?: unknown } | null)?.detail;
      if (typeof rawDetail === 'string') {
        detail = rawDetail;
      } else if (rawDetail) {
        detail = JSON.stringify(rawDetail);
      }
      const label = detail
        ? `API error ${response.status}: ${detail}`
        : `API error: ${response.status} ${response.statusText}`;
      throw new ApiError(response.status, label, detail);
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

  // Per-worker liveness of the consolidated `workers` supervisor (System page).
  // Maps the snake_case backend payload to the camelCase WorkerStatus type.
  async getWorkerStatus(): Promise<WorkerStatus> {
    const raw = await this.request<{
      available: boolean;
      heartbeat_age_seconds: number | null;
      workers: Array<{
        name: string;
        state: string;
        restart_count: number;
        last_error: string | null;
      }>;
    }>('/api/admin/runtime/workers');
    const KNOWN: WorkerLiveness['state'][] = [
      'starting',
      'running',
      'restarting',
      'stopped',
      'unknown',
    ];
    return {
      available: !!raw.available,
      heartbeatAgeSeconds: raw.heartbeat_age_seconds ?? null,
      workers: (raw.workers ?? []).map((w) => {
        // Validate against the known set — a plain `as` cast would let an
        // unrecognised backend state pass through and render a raw i18n key.
        const s = w.state as WorkerLiveness['state'];
        return {
          name: w.name,
          state: KNOWN.includes(s) ? s : 'unknown',
          restartCount: w.restart_count ?? 0,
          lastError: w.last_error ?? null,
        };
      }),
    };
  }

  // ── Home Assistant (smart-home) binding — per-agent ──
  async getHABinding(
    agentId: string,
  ): Promise<{ bound: boolean; base_url?: string; verify_tls?: boolean; token_masked?: string; corrupted?: boolean }> {
    return this.request(`/api/home-assistant/binding?agent_id=${encodeURIComponent(agentId)}`);
  }

  async saveHABinding(
    agentId: string,
    baseUrl: string,
    token: string,
    verifyTls: boolean,
  ): Promise<{ ok: boolean }> {
    return this.request(`/api/home-assistant/binding`, {
      method: 'PUT',
      body: JSON.stringify({ agent_id: agentId, base_url: baseUrl, token, verify_tls: verifyTls }),
    });
  }

  async testHAConnection(
    baseUrl: string,
    token: string,
    verifyTls: boolean,
  ): Promise<{ ok: boolean; entity_count?: number; error?: string }> {
    return this.request(`/api/home-assistant/test`, {
      method: 'POST',
      body: JSON.stringify({ base_url: baseUrl, token, verify_tls: verifyTls }),
    });
  }

  // Ping the SAVED binding via the same path the agent uses (proves the agent can reach HA).
  async verifyHABinding(agentId: string): Promise<{ ok: boolean; entity_count?: number; error?: string }> {
    return this.request(`/api/home-assistant/verify`, {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId }),
    });
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

  // Semantic search over Social Network Entities
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

  async getSimpleChatHistory(
    agentId: string,
    limit: number = 20,
    offset: number = 0,
    include: 'chat' | 'activity' | 'all' = 'all',
  ): Promise<SimpleChatHistoryResponse> {
    const params = new URLSearchParams({
      limit: limit.toString(),
      offset: offset.toString(),
      include,
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

  /**
   * Ask a run to stop. Owner only — the server rejects anyone else with 403
   * regardless of whether the caller rendered a button.
   *
   * Resolves as soon as the request is recorded, NOT when the run has
   * stopped: the run lives in another process and is interrupted there. The
   * caller should render "stopping" off this resolution and watch the run's
   * observation stream for the terminal state.
   */
  async cancelRun(runId: string): Promise<CancelRunResponse> {
    return this.request<CancelRunResponse>(
      `/api/runs/${encodeURIComponent(runId)}/cancel`,
      { method: 'POST' },
    );
  }

  /** The team's work board, INCLUDING parked items — a stopped task must not
   *  look deleted, and un-parking it is the user's call. */
  async getTeamWorkBoard(teamId: string): Promise<TeamWorkBoardResponse> {
    return this.request<TeamWorkBoardResponse>(
      `/api/teams/${encodeURIComponent(teamId)}/work-items`
    );
  }

  /** Un-park an item a stop parked. */
  async resumeTeamWorkItem(teamId: string, itemId: string): Promise<TeamOperationResponse> {
    return this.request<TeamOperationResponse>(
      `/api/teams/${encodeURIComponent(teamId)}/work-items/${encodeURIComponent(itemId)}/resume`,
      { method: 'POST' },
    );
  }

  /** Turn the Leader's periodic sweep on or off. The board stays usable either
   *  way — this only stops the chasing. Times out (→ rejects → the store rolls
   *  back) rather than pinning the in-flight state indefinitely. */
  async setTeamPatrol(teamId: string, enabled: boolean): Promise<TeamOperationResponse> {
    return this.request<TeamOperationResponse>(
      `/api/teams/${encodeURIComponent(teamId)}/patrol`,
      // Bounded: the store marks the switch "in flight" until this settles
      // (polls ignored, button disabled), so a request that never settles
      // must not be able to hold that state forever.
      { method: 'PUT', body: JSON.stringify({ enabled }), signal: AbortSignal.timeout(PATROL_WRITE_TIMEOUT_MS) },
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

  /**
   * Self-serve signup, step 1: ask NetMind to email a 6-digit code.
   *
   * Goes through OUR backend rather than NetMind directly, because the
   * send-code endpoint sends mail on our behalf and therefore needs a rate
   * limit — and a limit that lives in the page is not a limit.
   */
  async sendSignupCode(email: string): Promise<ApiResponse> {
    return this.request<ApiResponse>('/api/auth/signup/send-code', {
      method: 'POST',
      body: JSON.stringify({ email }),
    });
  }

  /**
   * Self-serve signup, step 2: create the account.
   *
   * Returns success only — no session. The caller signs in afterwards with the
   * credentials it already holds, so no token is ever minted by a route that
   * also accepts an unverified email.
   */
  async signup(email: string, password: string, verifyCode: string): Promise<ApiResponse> {
    return this.request<ApiResponse>('/api/auth/signup', {
      method: 'POST',
      body: JSON.stringify({ email, password, verify_code: verifyCode }),
    });
  }

  async netmindLogin(netmindToken: string, source?: string): Promise<NetmindLoginResponse> {
    const res = await this.request<NetmindLoginResponse>('/api/auth/netmind-login', {
      method: 'POST',
      body: JSON.stringify({ netmind_token: netmindToken, source: source || undefined }),
    });
    // Every cloud login flow (login page, ?token= pass-through, OAuth
    // callback) converges HERE, so this is the one place that reliably sees
    // is_new_user — the flag that arms the one-shot "create your own agent"
    // coachmark for users whose first agent was auto-provisioned server-side.
    // guide_agent_provisioning gates it on the SERVER's kill-switch: with
    // provisioning disabled, the coachmark would promise an agent that never
    // appears.
    if (res.success && res.is_new_user && res.guide_agent_provisioning) {
      markGuideCoachmarkPending();
    }
    return res;
  }

  /**
   * Report a client-side auth-funnel failure (fire-and-forget).
   *
   * The login step talks to NetMind directly from the browser, so when it
   * fails the server never learns a login was even attempted — this closes
   * that blind spot. Must never throw or surface: it is diagnostics riding
   * on top of a failure the user is already looking at.
   */
  reportAuthFunnel(stage: AuthFunnelStage, email?: string, detail?: string): void {
    void this.request('/api/auth/funnel-report', {
      method: 'POST',
      body: JSON.stringify({
        stage,
        email: email || undefined,
        detail: (detail || '').slice(0, 300),
      }),
    }).catch(() => undefined);
  }

  async createUser(userId: string, displayName?: string): Promise<CreateUserResponse> {
    const res = await this.request<CreateUserResponse>('/api/auth/create-user', {
      method: 'POST',
      body: JSON.stringify({
        user_id: userId,
        display_name: displayName,
      }),
    });
    // Local-mode signup is by definition a brand-new user — arm the same
    // "create your own agent" coachmark the cloud path arms via is_new_user,
    // under the same server-side kill-switch gate.
    if (res.success && res.guide_agent_provisioning) markGuideCoachmarkPending();
    return res;
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

  /** Read the current user's onboarding progress. The root redirect asks for
   *  `landing_completed` to decide whether this user still owes the one-time
   *  welcome flow — server-side state, so a new browser doesn't replay it. */
  async getOnboarding(): Promise<OnboardingResponse> {
    return this.request<OnboardingResponse>('/api/auth/onboarding');
  }

  /** Mark a single onboarding step complete. Write-once-true on the
   *  backend — passing a step here can only ever set it, never clear it.
   *  `landing_completed` is written by the welcome flow (on finish, on skip,
   *  and as a silent backfill for users who predate the flow). */
  async markOnboardingStep(
    userId: string,
    // 'dismissed' left the union with the checklist card (its only setter);
    // the backend still accepts it, so re-adding is a one-line change.
    step: 'first_agent_created' | 'template_applied' | 'landing_completed',
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

  /** The user's persisted reply-language preference; null = never set. */
  async getReplyLanguage(): Promise<string | null> {
    const r = await this.request<{ language: string | null }>(
      '/api/auth/settings/reply-language',
    );
    return r.language ?? null;
  }

  /** Persist the reply-language preference (i18n code; '' clears). The
   *  backend injects it into the agent's system prompt — without this
   *  write the language choice never reaches the model. */
  async setReplyLanguage(language: string): Promise<void> {
    await this.request<{ success: boolean; language: string | null }>(
      '/api/auth/settings/reply-language',
      {
        method: 'PUT',
        body: JSON.stringify({ language }),
      },
    );
  }

  /** Telemetry (diagnostic log shipping) consent state. Unlike
   *  analytics (per-user, DB row) this is a per-MACHINE marker file:
   *  `controllable` is false when a deployment env override or a
   *  multi-tenant cloud install owns the decision — render the toggle
   *  read-only then. */
  async getTelemetryConsent(): Promise<TelemetryConsentState> {
    return this.request<TelemetryConsentState>('/api/auth/settings/telemetry');
  }

  /** Flip the telemetry opt-out marker (per user account on the
   *  host). Opting out takes effect within one flush interval;
   *  re-enabling needs a restart only when telemetry was already off
   *  at process start. */
  async setTelemetryOptOut(optedOut: boolean): Promise<void> {
    await this.request<{ success: boolean; opted_out: boolean }>(
      '/api/auth/settings/telemetry',
      {
        method: 'PUT',
        body: JSON.stringify({ opted_out: optedOut }),
      },
    );
  }

  async getAgents(): Promise<AgentListResponse> {
    return this.request<AgentListResponse>(`/api/auth/agents`);
  }

  // In-app browser runtime. Routed through `request<T>` like everything else:
  // a raw fetch() here misses the identity headers the local-mode middleware
  // requires, and the 401 that produces is indistinguishable, in the UI, from
  // "no browser installed" — which is exactly what shipped on 2026-09-22.
  async getBrowserRuntime(): Promise<BrowserRuntimeStatus> {
    return this.request<BrowserRuntimeStatus>(`/api/browser/runtime`);
  }

  async setBrowserSource(source: 'managed' | 'system'): Promise<BrowserRuntimeStatus> {
    return this.request<BrowserRuntimeStatus>('/api/browser/runtime/source', {
      method: 'PUT', body: JSON.stringify({ source }),
    });
  }

  async setBrowserMode(mode: 'headless' | 'headed'): Promise<BrowserRuntimeStatus> {
    return this.request<BrowserRuntimeStatus>('/api/browser/runtime/mode', {
      method: 'PUT', body: JSON.stringify({ mode }),
    });
  }

  async installBrowserRuntime(): Promise<BrowserInstallResult> {
    return this.request<BrowserInstallResult>(`/api/browser/runtime/install`, { method: 'POST' });
  }

  async cancelBrowserInstall(): Promise<{ ok: boolean }> {
    return this.request<{ ok: boolean }>(`/api/browser/runtime/install/cancel`, { method: 'POST' });
  }

  async getBrowserPolicy(agentId: string): Promise<BrowserPolicyView> {
    return this.request<BrowserPolicyView>(`/api/browser/policy/${encodeURIComponent(agentId)}`);
  }

  async updateBrowserPolicy(agentId: string, rule: BrowserPolicyRule): Promise<BrowserPolicyView> {
    return this.request<BrowserPolicyView>(`/api/browser/policy/${encodeURIComponent(agentId)}`, {
      method: 'PUT', body: JSON.stringify(rule),
    });
  }

  async revokeBrowserPolicy(agentId: string, target: { origin: string; capability: BrowserPolicyCapability }): Promise<BrowserPolicyView> {
    return this.request<BrowserPolicyView>(`/api/browser/policy/${encodeURIComponent(agentId)}/revoke`, {
      method: 'POST', body: JSON.stringify(target),
    });
  }

  /** The agent's open login / verification requests (raised in the MCP host). */
  async getBrowserNotices(agentId: string): Promise<{ pending: BrowserLoginRequest[] }> {
    return this.request<{ pending: BrowserLoginRequest[] }>(
      `/api/browser/notices/${encodeURIComponent(agentId)}`,
    );
  }

  /** Liveness of the caller's own session. Does no database work server-side,
   *  so it is the cheap way to ask "is my JWT still accepted?" — used by
   *  ProtectedRoute on mount. (lib/sessionGuard probes the same endpoint with
   *  a raw fetch, deliberately bypassing `request<T>` to avoid recursing into
   *  the very 401 handler it serves.) */
  async getSession(): Promise<SessionResponse> {
    return this.request<SessionResponse>('/api/auth/session');
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
      throw new ApiError(response.status, `API error: ${response.status} ${response.statusText}`);
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
      throw new ApiError(response.status, `API error: ${response.status} ${response.statusText}`);
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
      throw new ApiError(response.status, `API error: ${response.status} ${response.statusText}`);
    }
    return response.blob();
  }

  /**
   * Fetch a bus-message attachment's bytes as a Blob (JWT/X-User-Id attached).
   * Bus attachments live in the per-user shared area and are addressed by
   * `rel_path` (from a message's `attachments` entry), served by
   * `GET /api/agent-inbox/attachments/raw`. Bypasses `request<T>` — binary body.
   */
  async fetchBusAttachmentBlob(relPath: string): Promise<Blob> {
    const url = `${getApiBaseUrl()}/api/agent-inbox/attachments/raw?path=${encodeURIComponent(relPath)}`;
    const response = await fetch(url, {
      method: 'GET',
      headers: this.getAuthHeaders(),
    });
    if (!response.ok) {
      throw new ApiError(response.status, `API error: ${response.status} ${response.statusText}`);
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
      throw new ApiError(response.status, `API error: ${response.status} ${response.statusText}`);
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
      throw new ApiError(response.status, error.detail || `API error: ${response.status} ${response.statusText}`);
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
      throw new ApiError(response.status, error.detail || `API error: ${response.status} ${response.statusText}`);
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

  // Skill Marketplace API — /api/marketplace/skills/* (read endpoints are
  // public; agent_id-scoped calls carry identity headers like the rest).
  async searchMarketplaceSkills(params: {
    q?: string;
    category?: string;
    capability?: string;
    sort?: 'downloads' | 'published' | 'name';
    page?: number;
    limit?: number;
    agentId?: string;
  }): Promise<MarketplaceSearchResponse> {
    const search = new URLSearchParams();
    if (params.q) search.set('q', params.q);
    if (params.category) search.set('category', params.category);
    if (params.capability) search.set('capability', params.capability);
    if (params.sort) search.set('sort', params.sort);
    if (params.page) search.set('page', String(params.page));
    if (params.limit) search.set('limit', String(params.limit));
    if (params.agentId) search.set('agent_id', params.agentId);
    return this.request<MarketplaceSearchResponse>(
      `/api/marketplace/skills/search?${search}`,
      { signal: AbortSignal.timeout(MARKETPLACE_SEARCH_TIMEOUT_MS) },
    );
  }

  async getMarketplaceSkillDetail(skillId: string): Promise<MarketplaceSkillDetail> {
    return this.request<MarketplaceSkillDetail>(
      `/api/marketplace/skills/${encodeURIComponent(skillId)}`
    );
  }

  async installMarketplaceSkill(
    skillId: string,
    agentId: string,
    version?: string
  ): Promise<MarketplaceInstallResponse> {
    return this.request<MarketplaceInstallResponse>(
      `/api/marketplace/skills/${encodeURIComponent(skillId)}/install`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_id: agentId, ...(version ? { version } : {}) }),
      }
    );
  }

  async checkSkillUpdates(agentId: string): Promise<{ updates: SkillUpdateInfo[] }> {
    const params = new URLSearchParams({ agent_id: agentId });
    return this.request<{ updates: SkillUpdateInfo[] }>(
      `/api/marketplace/skills/updates?${params}`
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
      providers: Record<string, ProviderRow>;
      slots: Record<string, unknown>;
      version?: number;
    };
  }> {
    return this.request(`/api/providers`);
  }

  /** Server-owned provider catalogue. The UI only reads ``onboard_defaults``
   * (per-source default agent/helper model ids; netmind_free follows the
   * FREE_TIER_*_MODEL ops env) — see lib/onboardDefaults.ts. */
  async getProviderCatalog(): Promise<{
    success: boolean;
    onboard_defaults: Record<string, { agent?: string; helper?: string }>;
  }> {
    return this.request(`/api/providers/catalog`);
  }

  /** Add a provider card (claude_oauth / codex_oauth / one of the key
   * card types). Business rejections arrive as HTTPException -> this
   * THROWS ApiError (read err.detail for the backend's reason); map
   * errors to user copy with providersApi.providerErrorMessage. */
  async addProvider(
    body: Record<string, unknown>,
  ): Promise<{ success: boolean; detail?: string }> {
    return this.request(`/api/providers`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  /** Claude Code CLI credential status (cloud non-staff get
   * allowed: false — the OAuth-cards gate). */
  async getClaudeStatus(): Promise<{
    success: boolean;
    data?: CliStatusPayload;
  }> {
    return this.request(`/api/providers/claude-status`);
  }

  /** Codex CLI credential status — same shape and gate as claude's. */
  async getCodexStatus(): Promise<{
    success: boolean;
    data?: CliStatusPayload;
  }> {
    return this.request(`/api/providers/codex-status`);
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
    /** False = register-only: the key was saved but framework/slots were NOT
     * rewired (cloud non-staff — slots stay on NetMind). The UI must not
     * claim "you're now running on <model>" in that case. */
    activated?: boolean;
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

  /** Get the user's coding-agent framework choice + auth probe.
   *
   * `frameworks` reports plugin-install availability per framework name
   * (Claude Code / Codex CLI are user-installed local plugins — see
   * `getPlugins`). The array only lists the plugin-gated frameworks, so a
   * framework absent from it (nexus_power, or any future non-plugin framework)
   * is treated as available — that "unknown ⇒ available" default lives in
   * lib/agentFramework.ts `frameworkAvailabilityMap`, and is why the field is
   * modelled as optional here.
   *
   * `display_name` / `protocol` / `oauth_source` (B6, 2026-09-07 final cut) come straight off
   * the framework registry's own metadata — `display_name` is the label
   * `lib/frameworkBrand.ts`'s `formatFrameworkFromList` prefers over the static picker table;
   * `protocol`/`oauth_source` are the ONLY source `lib/agentFramework.ts`'s
   * `providerBacksFramework` reads for its matching — that function has no hardcoded table left
   * to fall back to, so a caller MUST pass this array once loaded, or it fails closed. `protocol`
   * is the backend's `FrameworkMeta.protocol` three-valued enum: a specific protocol, or `'any'`
   * for a framework (NexusPower) that drives the provider API itself and works with either — a
   * caller must NOT branch on framework id/name to special-case this, only on the `'any'` value.
   * All three fields are still optional on the type — an older backend, or a request made before
   * this list has ever loaded, has none of them.
   */
  async getAgentFramework(): Promise<{
    success: boolean;
    data: {
      framework: string;
      supported: string[];
      frameworks?: Array<{ name: string; available: boolean; display_name?: string; protocol?: 'anthropic' | 'openai' | 'any'; oauth_source?: string | null }>;
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
      /** The bound agent provider couldn't back the new framework and was
       *  unbound server-side — the editor has to drop it from its draft. */
      slot_cleared: boolean;
    };
  }> {
    return this.request(`/api/providers/agent-framework`, {
      method: 'POST',
      body: JSON.stringify({ framework }),
    });
  }

  // ── local plugin installs (Settings › Plugins) ─────────────────────────
  // Claude Code / Codex CLI ship as user-installed plugins rather than
  // baked into the desktop image. `cloud_managed` in the response tells the
  // panel to hide itself entirely — cloud installs its own CLIs centrally.

  // ---- plugin factory (/api/plugin-factory): user plugins from GitHub / local dirs.

  async factoryList(): Promise<FactoryListResponse> {
    return this.request(`/api/plugin-factory`);
  }

  async factoryInstall(source: string, opts: { permissionsAcknowledged?: boolean; scope?: string } = {}): Promise<FactoryInstallResponse> {
    return this.request(`/api/plugin-factory/install`, {
      method: 'POST',
      body: JSON.stringify({ source, permissions_acknowledged: opts.permissionsAcknowledged ?? false, scope: opts.scope ?? 'global' }),
    });
  }

  async factoryAction(
    id: string,
    action: 'enable' | 'disable' | 'uninstall' | 'upgrade' | 'acknowledge-permissions',
    opts: { permissionsAcknowledged?: boolean } = {},
  ): Promise<ApiResponse> {
    // Only the acknowledge action carries a body: it is the one write that must record
    // `permissions_acknowledged = true` server-side, the durable flag the disclosure modal
    // uses to decide whether it must reopen after a reload.
    const body = action === 'acknowledge-permissions' ? JSON.stringify({ permissions_acknowledged: opts.permissionsAcknowledged ?? true }) : undefined;
    return this.request(`/api/plugin-factory/${encodeURIComponent(id)}/${action}`, { method: 'POST', ...(body !== undefined ? { body } : {}) });
  }

  async factoryBuiltinSetEnabled(id: string, enabled: boolean): Promise<ApiResponse & { data?: { id: string; enabled: boolean; also_disabled: string[]; restart_required: boolean } }> {
    return this.request(`/api/plugin-factory/builtin/${encodeURIComponent(id)}/${enabled ? 'enable' : 'disable'}`, { method: 'POST' });
  }

  async factoryBuiltinInstallDeps(id: string): Promise<ApiResponse & { data?: { id: string; installed: string[]; restart_required: boolean } }> {
    return this.request(`/api/plugin-factory/builtin/${encodeURIComponent(id)}/install-deps`, { method: 'POST' });
  }

  async factoryRollback(): Promise<ApiResponse> {
    return this.request(`/api/plugin-factory/rollback`, { method: 'POST' });
  }

  async factoryLeaveSafeMode(): Promise<ApiResponse> {
    return this.request(`/api/plugin-factory/safe-mode/leave`, { method: 'POST' });
  }

  async factoryBisect(step: 'start' | 'stop'): Promise<ApiResponse & { data?: { trial?: string[]; remaining?: number; culprit?: string | null } }> {
    return this.request(`/api/plugin-factory/bisect/${step}`, { method: 'POST' });
  }

  async factoryBisectAnswer(good: boolean): Promise<ApiResponse & { data?: { trial: string[]; remaining: number; culprit: string | null } }> {
    return this.request(`/api/plugin-factory/bisect/answer`, { method: 'POST', body: JSON.stringify({ good }) });
  }

  async factoryProposals(): Promise<FactoryProposalsResponse> {
    return this.request(`/api/plugin-factory/proposals`);
  }

  async factoryDecide(proposalId: string, approved: boolean): Promise<ApiResponse & { data?: { decision: string; restart_required?: boolean } }> {
    return this.request(`/api/plugin-factory/proposals/${encodeURIComponent(proposalId)}/decide`, { method: 'POST', body: JSON.stringify({ approved }) });
  }

  async factoryErrors(id: string): Promise<FactoryErrorsResponse> {
    return this.request(`/api/plugin-factory/${encodeURIComponent(id)}/errors`);
  }

  async factoryIndex(q = ''): Promise<FactoryIndexResponse> {
    return this.request(`/api/plugin-factory/index?q=${encodeURIComponent(q)}`);
  }

  /** List every known plugin's install/update/login state. */
  async getPlugins(): Promise<PluginsListResponse> {
    return this.request(`/api/plugins`);
  }

  /** Remove a plugin's local install (binary + its pip/npm target dir). */
  async uninstallPlugin(id: PluginId): Promise<PluginUninstallResponse> {
    return this.request(`/api/plugins/${encodeURIComponent(id)}/uninstall`, {
      method: 'POST',
    });
  }

  /**
   * Streams the ndjson install log for a plugin. `onEvent` fires once per
   * line as it arrives off the wire — progress frames (`done: false`) carry
   * one pip/npm log line each; the stream always ends with exactly one
   * `done: true` frame carrying the outcome, which this also returns.
   *
   * Bypasses `request<T>` (like the FormData/blob helpers) because the
   * response body is a line-delimited stream, not a single JSON document —
   * `response.body.getReader()` is read incrementally instead of calling
   * `response.json()` once.
   */
  async installPlugin(
    id: PluginId,
    onEvent: (event: PluginInstallEvent) => void,
  ): Promise<Extract<PluginInstallEvent, { done: true }>> {
    const baseUrl = getApiBaseUrl();
    const response = await fetch(`${baseUrl}/api/plugins/${encodeURIComponent(id)}/install`, {
      method: 'POST',
      headers: { ...this.getAuthHeaders() },
    });

    if (!response.ok || !response.body) {
      let detail = '';
      try {
        const body = (await response.json()) as { detail?: unknown };
        if (typeof body?.detail === 'string') detail = body.detail;
      } catch {
        /* not JSON — fall through to the status line */
      }
      throw new ApiError(
        response.status,
        detail || `API error: ${response.status} ${response.statusText}`,
      );
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let final: Extract<PluginInstallEvent, { done: true }> | null = null;

    const consumeLine = (line: string) => {
      const trimmed = line.trim();
      if (!trimmed) return;
      const event = JSON.parse(trimmed) as PluginInstallEvent;
      onEvent(event);
      if (event.done) final = event;
    };

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newlineIndex = buffer.indexOf('\n');
      while (newlineIndex !== -1) {
        consumeLine(buffer.slice(0, newlineIndex));
        buffer = buffer.slice(newlineIndex + 1);
        newlineIndex = buffer.indexOf('\n');
      }
    }
    if (buffer) consumeLine(buffer);

    if (!final) {
      throw new Error('Plugin install stream ended without a result');
    }
    return final;
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

  // ---- per-agent capabilities (plugin platform batch 5c): which registered modules take part in this agent's turns.
  async getAgentCapabilities(agentId: string): Promise<{ success: boolean; detail?: string; data?: AgentCapabilitiesView }> {
    return this.request(`/api/agents/${encodeURIComponent(agentId)}/capabilities`);
  }

  async setAgentCapability(agentId: string, moduleClass: string, enabled: boolean): Promise<{ success: boolean; detail?: string; data?: { module_class: string; enabled: boolean } }> {
    return this.request(`/api/agents/${encodeURIComponent(agentId)}/capabilities/${encodeURIComponent(moduleClass)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
  }

  async resetAgentCapability(agentId: string, moduleClass: string): Promise<{ success: boolean; data?: { module_class: string; reset: boolean } }> {
    return this.request(`/api/agents/${encodeURIComponent(agentId)}/capabilities/${encodeURIComponent(moduleClass)}`, { method: 'DELETE' });
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

  /** How many agents currently hold a per-agent override, per slot — the
   *  blast radius shown before applying defaults to all agents. */
  async getSlotOverrideStats(): Promise<{ success: boolean; data?: SlotOverrideStats }> {
    return this.request(`/api/providers/slots/override-stats`);
  }

  /** Clear the given slots' per-agent overrides across ALL the caller's
   *  agents (clear-to-inherit). */
  async applySlotsToAgents(
    slots: string[],
  ): Promise<{ success: boolean; detail?: string; data?: { cleared: Record<string, number> } }> {
    return this.request(`/api/providers/slots/apply-to-agents`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slots }),
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

  /** Edit a job's execution time (schedule rule). Only send the changed fields;
   *  omitted fields keep their existing trigger_config value. Throws ApiError
   *  on 400 (invalid schedule / non-editable status) / 403 / 404. */
  async updateJobSchedule(
    jobId: string,
    fields: { run_at?: string; cron?: string; interval_seconds?: number; timezone?: string },
  ): Promise<{ success: boolean; job_id: string; next_run_at?: string; next_run_timezone?: string }> {
    return this.request(`/api/dashboard/jobs/${encodeURIComponent(jobId)}/schedule`, {
      method: 'PUT',
      body: JSON.stringify(fields),
    });
  }

  /** Edit a job's content fields (title/description/payload) — the seam
   *  route PUT /api/jobs/{job_id}, which mirrors the job_update MCP tool
   *  exactly. Only send the changed fields; omitted fields (undefined) keep
   *  their existing value server-side. `agentId` is the owning agent, used
   *  for ownership scoping (assert_owned), not the job's own id. */
  async updateJob(
    jobId: string,
    agentId: string,
    fields: { title?: string; description?: string; payload?: string },
  ): Promise<{ success: boolean; job_id?: string; updated_fields?: string[]; message?: string }> {
    return this.request(`/api/jobs/${encodeURIComponent(jobId)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: agentId, ...fields }),
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

  /** The `ingress.channels` catalog: one row per channel plugin the host loaded, with its
   *  `ChannelUi` (label / lucide icon name / order) and owning plugin id. `registerBuiltinChannels`
   *  builds the Channels section from this instead of restating each row in TypeScript. */
  async pluginChannels(): Promise<ApiResponse & { data?: PluginChannelRow[] }> {
    return this.request('/api/plugins/channels');
  }

  // Telegram Integration API
  // ---- generic channels (/api/channels/{channel}): every channel in ingress.channels — the six
  // builtins and plugin channels alike (plugin platform batch 4d.3 retired the per-channel
  // bind/credential/test/unbind/set-active routes). The type parameters let a builtin's config
  // component keep its precise credential / bind envelope (the service's response is returned
  // verbatim by the route); channel-specific flows (Lark OAuth, WeChat QR) keep their own methods.
  async channelSchema(channel: string): Promise<ApiResponse & { data?: ChannelSchema }> {
    return this.request(`/api/channels/${encodeURIComponent(channel)}/schema`);
  }

  async channelCredential<D extends object = ChannelCredentialView>(channel: string, agentId: string): Promise<ApiResponse & { data?: D | null }> {
    return this.request(`/api/channels/${encodeURIComponent(channel)}/credential?agent_id=${encodeURIComponent(agentId)}`);
  }

  async channelBind<R extends ApiResponse = ApiResponse & { data?: ChannelCredentialView }>(channel: string, agentId: string, fields: Record<string, unknown>): Promise<R> {
    return this.request<R>(`/api/channels/${encodeURIComponent(channel)}/bind`, { method: 'POST', body: JSON.stringify({ agent_id: agentId, fields }) });
  }

  async channelTest<R extends ApiResponse = ApiResponse & { data?: Record<string, unknown> }>(channel: string, agentId: string): Promise<R> {
    return this.request<R>(`/api/channels/${encodeURIComponent(channel)}/test`, { method: 'POST', body: JSON.stringify({ agent_id: agentId }) });
  }

  async channelUnbind(channel: string, agentId: string): Promise<ApiResponse> {
    return this.request(`/api/channels/${encodeURIComponent(channel)}/unbind`, { method: 'POST', body: JSON.stringify({ agent_id: agentId }) });
  }

  /** Flip a bound credential's active flag without re-binding (a bundle-imported binding goes live here). */
  async channelSetActive(channel: string, agentId: string, active: boolean): Promise<ApiResponse & { enabled?: boolean }> {
    return this.request(`/api/channels/${encodeURIComponent(channel)}/set-active`, { method: 'POST', body: JSON.stringify({ agent_id: agentId, active }) });
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

  // System-default free-tier quota
  async getMyQuota(): Promise<QuotaMeResponse> {
    return this.request<QuotaMeResponse>('/api/quota/me');
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
  //
  // `months` applies to the one-time products (alipay / wechat) ONLY. The
  // backend REJECTS it on a card subscription rather than ignoring it, so it is
  // omitted here instead of defaulted: sending "card, 6 months" would describe
  // a purchase that cannot happen.
  async subscribe(
    paymentMethod: SubscribePaymentMethod = 'stripe',
    months?: number,
  ): Promise<SubscribeResponse> {
    const token = this.getNetmindToken();
    if (!token) throw new Error('NetMind account not linked (no loginToken)');
    const body: Record<string, unknown> = { payment_method: paymentMethod };
    if (paymentMethod !== 'stripe') body.months = months ?? 1;
    return this.request<SubscribeResponse>('/api/billing/subscribe', {
      method: 'POST',
      headers: { 'X-Netmind-Token': token },
      body: JSON.stringify(body),
    });
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
  // `amount` is ALWAYS the USD credit being bought, even when the payer is
  // charged in CNY — the backend derives the charge currency from the method,
  // because upstream 400s when the two disagree.
  async recharge(
    amount: number,
    paymentMethod: RechargePaymentMethod = 'default',
  ): Promise<RechargeResponse> {
    const token = this.getNetmindToken();
    if (!token) throw new Error('NetMind account not linked (no loginToken)');
    return this.request<RechargeResponse>('/api/billing/recharge', {
      method: 'POST',
      headers: { 'X-Netmind-Token': token },
      body: JSON.stringify({ amount, payment_method: paymentMethod }),
    });
  }

  // What a CNY charge costs right now, for showing "$10 ≈ ¥73" BEFORE the user
  // commits. Quote-only: never call this to decide an amount, only to display
  // one — the charge is priced upstream at its own live rate.
  async fxRate(amount?: number): Promise<FxQuoteResponse> {
    const token = this.getNetmindToken();
    if (!token) throw new Error('NetMind account not linked (no loginToken)');
    const qs = amount !== undefined ? `?amount=${encodeURIComponent(amount)}` : '';
    return this.request<FxQuoteResponse>(`/api/billing/fx-rate${qs}`, {
      headers: { 'X-Netmind-Token': token },
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

  /**
   * A team's artifacts, newest first — the workspace panel's Artifacts tab.
   *
   * Not scoped to one agent: the panel shows the TEAM's output whoever made
   * it, and each row carries agent_id so the UI can attribute it.
   */
  async listTeamArtifacts(teamId: string): Promise<Artifact[]> {
    return this.request<Artifact[]>(
      `/api/teams/${encodeURIComponent(teamId)}/artifacts`,
    );
  }

  /**
   * Which artifacts each turn in this room produced (event_id → artifact ids).
   *
   * The transcript already carries each reply's event_id, so this is a real
   * join key. Matching by timestamp was never viable: one turn can register
   * two artifacts and two agents can answer at once, so proximity would pin
   * the wrong work to the wrong message.
   */
  async listTeamArtifactTurns(teamId: string): Promise<Record<string, string[]>> {
    return this.request<Record<string, string[]>>(
      `/api/teams/${encodeURIComponent(teamId)}/artifact-turns`,
    );
  }

  /** Files shared into the team folder, newest first — the Files tab. */
  async listTeamFiles(teamId: string): Promise<TeamFile[]> {
    return this.request<TeamFile[]>(
      `/api/teams/${encodeURIComponent(teamId)}/files`,
    );
  }

  /**
   * A team's bulletin plus its budget usage.
   *
   * Usage rides along with the list so the panel can grey out "add" before the
   * user types a rule instead of rejecting it afterwards.
   */
  async getTeamBulletin(teamId: string): Promise<TeamBulletin> {
    return this.request<TeamBulletin>(
      `/api/teams/${encodeURIComponent(teamId)}/bulletin`,
    );
  }

  async createTeamBulletinEntry(
    teamId: string,
    payload: { content: string; tier?: 'long_term' | 'current_task' },
  ): Promise<{ success: boolean; entry?: BulletinEntry; error?: string }> {
    return this.request(`/api/teams/${encodeURIComponent(teamId)}/bulletin`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async updateTeamBulletinEntry(
    teamId: string,
    entryId: string,
    content: string,
  ): Promise<{ success: boolean; error?: string }> {
    return this.request(
      `/api/teams/${encodeURIComponent(teamId)}/bulletin/${encodeURIComponent(entryId)}`,
      { method: 'PATCH', body: JSON.stringify({ content }) },
    );
  }

  async deleteTeamBulletinEntry(
    teamId: string,
    entryId: string,
  ): Promise<{ success: boolean; error?: string }> {
    return this.request(
      `/api/teams/${encodeURIComponent(teamId)}/bulletin/${encodeURIComponent(entryId)}`,
      { method: 'DELETE' },
    );
  }

  /** Clear one tier in a single action — the "this task is done" button. */
  async clearTeamBulletinTier(
    teamId: string,
    tier: 'long_term' | 'current_task',
  ): Promise<{ success: boolean; removed?: number; error?: string }> {
    const params = new URLSearchParams({ tier });
    return this.request(
      `/api/teams/${encodeURIComponent(teamId)}/bulletin?${params}`,
      { method: 'DELETE' },
    );
  }

  async createTeam(payload: { name: string; description?: string; color?: string }): Promise<TeamOperationResponse> {
    return this.request<TeamOperationResponse>('/api/teams', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async updateTeam(teamId: string, patch: { name?: string; description?: string; color?: string; intro_md?: string; lead_agent_id?: string }): Promise<TeamOperationResponse> {
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

  /** Clear a team's data (group-chat and/or shared files), keeping the team,
   *  its members and the bus channel. Team counterpart to clearHistory. */
  async clearTeamData(
    teamId: string,
    opts: { chat: boolean; files: boolean; bulletin?: boolean } = { chat: true, files: true },
  ): Promise<{
    success: boolean;
    chat_messages?: number;
    files_removed?: boolean;
    bulletin_entries?: number;
    error?: string;
  }> {
    const params = new URLSearchParams({
      chat: String(opts.chat),
      files: String(opts.files),
      bulletin: String(opts.bulletin ?? false),
    });
    return this.request(
      `/api/teams/${encodeURIComponent(teamId)}/data?${params}`,
      { method: 'DELETE' },
    );
  }

  // --- Team group chat (over the message bus) ---

  /**
   * A page of a team room's transcript.
   *
   * Three modes, and the cursors are mutually exclusive: no cursor is the
   * NEWEST page (what the room opens on), `since` walks forward from what is on
   * screen (the poll), `before` walks back into history (load more).
   */
  async getTeamChat(
    teamId: string,
    since?: string,
    before?: string,
  ): Promise<TeamChatHistoryResponse> {
    const params = new URLSearchParams();
    if (before) params.set('before', before);
    else if (since) params.set('since', since);
    const q = params.toString() ? `?${params}` : '';
    return this.request<TeamChatHistoryResponse>(
      `/api/teams/${encodeURIComponent(teamId)}/chat/messages${q}`,
    );
  }

  /** Post a user message into a team's group chat. `mentions` carries
   *  agent_ids and/or the literal "@all" (the backend maps it to @everyone).
   *  `attachments` are bus-attachment dicts from `uploadTeamChatAttachment`. */
  async sendTeamChat(
    teamId: string,
    content: string,
    mentions: string[],
    attachments: BusAttachment[] = [],
  ): Promise<TeamChatSendResponse> {
    return this.request<TeamChatSendResponse>(
      `/api/teams/${encodeURIComponent(teamId)}/chat/messages`,
      { method: 'POST', body: JSON.stringify({ content, mentions, attachments }) },
    );
  }

  /** Upload a file to attach to a team chat message. Returns the
   *  bus-attachment dict to echo back in `sendTeamChat`. Multipart, so it
   *  bypasses `request<T>` (which forces application/json). */
  async uploadTeamChatAttachment(
    teamId: string,
    file: File,
    options?: { source?: 'recording' | 'upload' },
  ): Promise<{
    success: boolean;
    attachment?: BusAttachment;
    transcription_available?: boolean | null;
    error?: string;
  }> {
    const formData = new FormData();
    formData.append('file', file);
    const params = new URLSearchParams();
    if (options?.source) params.set('source', options.source);
    const qs = params.toString();
    const url = `${getApiBaseUrl()}/api/teams/${encodeURIComponent(teamId)}/chat/attachments${qs ? `?${qs}` : ''}`;
    const response = await fetch(url, { method: 'POST', body: formData, headers: this.getAuthHeaders() });
    if (!response.ok) {
      throw new ApiError(response.status, `API error: ${response.status} ${response.statusText}`);
    }
    return response.json();
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

  // Team Marketplace — /api/marketplace/teams/*
  async getTeamTemplates(): Promise<TeamMarketplaceListResponse> {
    return this.request<TeamMarketplaceListResponse>('/api/marketplace/teams/templates');
  }

  async getTeamTemplate(templateId: string): Promise<TeamTemplate> {
    return this.request<TeamTemplate>(
      `/api/marketplace/teams/templates/${encodeURIComponent(templateId)}`,
    );
  }

  /** Resolve + verify the template bundle server-side and run the local
   *  importer preflight. Same response shape as a normal bundle preflight,
   *  so the existing review/confirm wizard consumes it unchanged. */
  async installTeamTemplatePreflight(
    templateId: string,
  ): Promise<BundlePreflightResponse> {
    return this.request<BundlePreflightResponse>(
      `/api/marketplace/teams/templates/${encodeURIComponent(templateId)}/install-preflight`,
      { method: 'POST', body: JSON.stringify({}) },
    );
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

  // ── Agent Migration (import from other frameworks) ─────────────────────
  // All three are LOCAL ONLY (503 on cloud) — Agent Migration reads the user's
  // filesystem, which cloud doesn't have.

  /** List every known framework found in the standard home locations. */
  async migrateDetect(): Promise<MigrationDetectResponse> {
    return this.request<MigrationDetectResponse>('/api/migrate/detect');
  }

  /** Scan one source into the standardized JSON (detect + extract, no write). */
  async migrateScan(
    path?: string,
    framework?: MigrationFramework,
  ): Promise<StandardizedAgentImport> {
    return this.request<StandardizedAgentImport>('/api/migrate/scan', {
      method: 'POST',
      body: JSON.stringify({ path, framework }),
    });
  }

  /** Execute the migration: create/populate an agent from the scanned JSON. */
  async migrateApply(
    importData: StandardizedAgentImport,
    agentId?: string,
    /** Handle for this one apply, so `migrateHurry` can reach it mid-flight. */
    importId?: string,
  ): Promise<MigrationApplyResult> {
    return this.request<MigrationApplyResult>('/api/migrate/apply', {
      method: 'POST',
      body: JSON.stringify({ import_data: importData, agent_id: agentId, import_id: importId }),
    });
  }

  /** Tell a RUNNING import to stop summarizing and just finish. The apply keeps
   *  going (cutting it would half-populate an agent) but drops to the
   *  deterministic no-LLM summary for every session it has left — which is the
   *  difference between seconds and N sequential model calls. */
  async migrateHurry(importId: string): Promise<{ success: boolean }> {
    return this.request<{ success: boolean }>('/api/migrate/hurry', {
      method: 'POST',
      body: JSON.stringify({ import_id: importId }),
    });
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
