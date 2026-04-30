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
}

// Error message
export interface ErrorMessage extends BaseMessage {
  type: 'error';
  error_message: string;
  error_type: string;
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

// Union type for all runtime messages
export type RuntimeMessage =
  | ProgressMessage
  | AgentTextDelta
  | AgentThinking
  | AgentToolCall
  | ErrorMessage
  | CompleteMessage
  | HeartbeatMessage
  | CancelledMessage;

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
}

// Chat message for display
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  thinking?: string;
  toolCalls?: AgentToolCall[];
  isError?: boolean;  // True when displaying runtime errors (rate limit, API errors, etc.)
  warnings?: string[];  // Non-fatal errors that occurred during execution (e.g., module decision LLM failed)
  attachments?: Attachment[];  // User-uploaded files referenced by this message
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
