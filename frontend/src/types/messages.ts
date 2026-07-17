/**
 * Runtime message type definitions
 * Matches the backend RuntimeMessage schema
 */

// Message type enum
export type MessageType =
  | 'progress'
  | 'agent_response'
  | 'agent_thinking'
  | 'tool_call'
  | 'error'
  | 'complete'
  | 'heartbeat'
  | 'cancelled';

// Progress status
export type ProgressStatus = 'running' | 'completed' | 'failed';

// Base message interface
export interface BaseMessage {
  type: MessageType;
  timestamp: number;
}

// Progress message - step-by-step execution
export interface ProgressMessage extends BaseMessage {
  type: 'progress';
  step: string;
  title: string;
  description: string;
  status: ProgressStatus;
  substeps: string[];
  details?: Record<string, unknown>;
}

// Agent text response (streaming)
export interface AgentTextDelta extends BaseMessage {
  type: 'agent_response';
  delta: string;
  response_type: 'text';
}

// Agent thinking process
export interface AgentThinking extends BaseMessage {
  type: 'agent_thinking';
  thinking_content: string;
}

// Tool/function call
export interface AgentToolCall extends BaseMessage {
  type: 'tool_call';
  tool_name: string;
  tool_input: Record<string, unknown>;
  tool_output?: string;
  // Backend tags the originating progress message with a step like
  // "3.4.{N}"; the matching tool_output progress shares the same step.
  // chatStore uses it to backfill tool_output onto the right call when
  // the output frame arrives (live or via reconnect replay).
  step?: string;
}

// Error message
export interface ErrorMessage extends BaseMessage {
  type: 'error';
  error_message: string;
  error_type: string;
  /**
   * - 'fatal': framework crashed and no recovery happened. Frontend shows
   *   this as the primary displayContent.
   * - 'recoverable': transient signal (rate-limit blip etc.) — the agent
   *   loop kept going. Surfaced as info.
   * - 'recovered': fatal-class error masked by a successful helper_llm
   *   after-error fallback reply. Frontend renders the recovered reply
   *   normally + a warning badge.
   * - 'recovered_after_reply': fatal hit AFTER the agent already replied
   *   organically; no helper_llm ran. Frontend renders the agent's own
   *   reply + a warning badge so the user knows the turn was truncated.
   * Optional for backwards-compat with older payloads.
   */
  severity?: 'fatal' | 'recoverable' | 'recovered' | 'recovered_after_reply';
  /**
   * Only set when error_type === 'config_actionable': the concrete, user-
   * self-serviceable reason so the UI can show "what you can do" guidance
   * (switch model / top up / fix model id) instead of a generic "turn
   * failed". These deterministic failures recur every turn with the same
   * config and are NOT masked by the helper-LLM fallback. Optional /
   * open-ended for forward-compat with new reasons.
   */
  action_reason?: 'context_window' | 'insufficient_balance' | 'model_not_found' | string;
  traceback?: string;
}

// Completion message
export interface CompleteMessage extends BaseMessage {
  type: 'complete';
  message: string;
}

// Heartbeat message - keep connection alive
export interface HeartbeatMessage extends BaseMessage {
  type: 'heartbeat';
}

// Cancelled message - user requested stop
export interface CancelledMessage extends BaseMessage {
  type: 'cancelled';
  message: string;
}

// Run-started control frame — the first meaningful frame on a fresh run.
// Carries the run/event id (= events.event_id) the client uses both for
// reconnect and for exact (role, event_id) timeline dedup. Standalone
// (not BaseMessage) because the backend frame has no `timestamp`.
export interface RunStartedMessage {
  type: 'run_started';
  run_id: string;
}

// Union type for all runtime messages
export type RuntimeMessage =
  | ProgressMessage
  | AgentTextDelta
  | AgentThinking
  | AgentToolCall
  | ErrorMessage
  | CompleteMessage
  | HeartbeatMessage
  | CancelledMessage
  | RunStartedMessage;

// Attachment metadata (mirrors backend xyz_agent_context.schema.Attachment)
export type AttachmentCategory =
  | 'image'
  | 'document'
  | 'code'
  | 'data'
  | 'media'
  | 'other';

export interface Attachment {
  file_id: string;
  mime_type: string;
  original_name: string;
  size_bytes: number;
  category: AttachmentCategory;
  // How the attachment was produced — echoed back from the upload
  // route so the renderer can dispatch without filename heuristics.
  // 'recording' = in-browser AudioRecorder voice memo (renders as
  // VoiceTranscript). 'upload' (or undefined on legacy rows) = regular
  // file upload (Paperclip / drag-drop / paste — renders as a file
  // chip even when an audio transcript is present).
  source?: 'recording' | 'upload';
  // Whisper-transcribed text for audio/* uploads. Forwarded through the
  // WebSocket payload so the agent's attachment marker carries the
  // transcript and the LLM reads it directly without a Read tool round
  // trip. None for non-audio uploads or when transcription was
  // unavailable / failed.
  transcript?: string;
}

// Chat message for display
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  // The run/event id this message belongs to (= events.event_id). Stamped
  // by chatStore: onto the user prompt when `run_started` arrives, and
  // onto the assistant reply at stopStreaming. It is the exact, formatting-
  // immune key used to dedup session messages against persisted history
  // rows (which carry the same event_id) — see lib/buildTimeline.ts.
  // Absent on legacy messages and on a session message created before
  // `run_started` landed; the timeline builder falls back to a
  // role:content heuristic for those.
  event_id?: string;
  thinking?: string;
  toolCalls?: AgentToolCall[];
  isError?: boolean;  // True when displaying runtime errors (rate limit, API errors, etc.)
  // Set when the turn failed a DETERMINISTIC, user-self-serviceable way
  // (config_actionable): carries the reason so MessageBubble renders "what
  // you can do" guidance (switch model / top up / fix model id) instead of a
  // generic failure. Pairs with isError=true (these turns produce no reply).
  actionReason?: 'context_window' | 'insufficient_balance' | 'model_not_found' | string;
  warnings?: string[];  // Non-fatal errors that occurred during execution (e.g., module decision LLM failed)
  attachments?: Attachment[];  // User-uploaded files referenced by this message
  // Inline timeline carried over from the live stream. Set on assistant
  // messages at stopStreaming time so MessageBubble can render exactly
  // the same chronological "think → tool → reply" sequence the user
  // just watched, collapsed by default behind "View reasoning & tools".
  // Reply events are kept here for fidelity; MessageBubble skips them
  // when rendering because message.content already shows the reply.
  timeline?: TurnEvent[];
}

// Step for display in StepsPanel
export interface Step {
  id: string;
  step: string;
  title: string;
  description: string;
  status: ProgressStatus;
  substeps: string[];
  details?: Record<string, unknown>;
  timestamp: number;
}

// Conversation round (for history)
export interface ConversationRound {
  id: string;
  userMessage: ChatMessage;
  assistantMessage: ChatMessage;
  steps: Step[];
  timestamp: number;
}

/**
 * TurnEvent — one block in the inline timeline that ChatPanel renders
 * during streaming. Built up from the raw websocket frames by
 * chatStore.processMessage and consumed by <TurnTimeline>.
 *
 * Design (see 2026-05-12 review with Xiong):
 * - thinking / tool_call / tool_output / reply / native_output are the
 *   only visible block types. Other progress frames (step markers like
 *   3.5, 4, 5) are framework plumbing and don't appear in the timeline.
 * - reply (from send_message_to_user_directly) is the authoritative
 *   user-facing speech; native_output (raw LLM text after the agent
 *   already used send_message in the same turn) is dropped at push
 *   time as a duplicate. Long-term the agent prompt should stop
 *   emitting that repetition — frontend dedup is a stopgap.
 * - All blocks carry their own `id` so per-block expand/collapse can
 *   be tracked across re-renders without losing state.
 */
export interface ThinkingEvent {
  type: 'thinking';
  id: string;
  ts: number;
  content: string;
}

export interface ToolCallEvent {
  type: 'tool_call';
  id: string;
  ts: number;
  tool_name: string;
  tool_input: Record<string, unknown>;
  tool_call_id?: string;
  reply_via?: string;
}

export interface ToolOutputEvent {
  type: 'tool_output';
  id: string;
  ts: number;
  tool_call_id?: string;
  tool_name: string;
  output: string;
}

export interface ReplyEvent {
  type: 'reply';
  id: string;
  ts: number;
  content: string;
  reply_via?: string;
}

export interface NativeOutputEvent {
  type: 'native_output';
  id: string;
  ts: number;
  content: string;
}

export type TurnEvent =
  | ThinkingEvent
  | ToolCallEvent
  | ToolOutputEvent
  | ReplyEvent
  | NativeOutputEvent;
