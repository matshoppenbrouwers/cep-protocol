/**
 * Common Event Protocol TypeScript types.
 *
 * Mirrors the Python reference implementation in `cep/types.py`. These types
 * define the wire contract between a harness adapter and any TypeScript client
 * that consumes the event stream.
 */

// Event types matching the Python EventType enum
export type ShellEventType =
  | "message_chunk"
  | "message_complete"
  | "tool_call_start"
  | "tool_call_end"
  | "thinking_block"
  | "approval_request"
  | "approval_response"
  | "error"
  | "status"
  | "turn_start"
  | "turn_end"
  | "user_message"
  | "context_update"
  | "cancel";

// Typed payloads
export interface MessageChunkPayload {
  text: string;
  done: boolean;
}

export interface MessageCompletePayload {
  text: string;
  role: string;
}

export interface ToolCallStartPayload {
  tool_name: string;
  args: Record<string, unknown>;
}

export interface ToolCallEndPayload {
  tool_name: string;
  result: string;
  success: boolean;
}

export interface ThinkingBlockPayload {
  text: string;
}

export interface ApprovalRequestPayload {
  action: string;
  description: string;
  // "advisory" marks informational approvals (the tool already ran
  // harness-side); the UI renders these as warnings, not gates.
  risk: "low" | "medium" | "high" | "advisory";
  // Correlation key echoed back in ApprovalResponsePayload.request_id.
  // Falls back to the ShellEvent.id of the approval_request when empty.
  request_id?: string;
}

export interface ApprovalResponsePayload {
  approved: boolean;
  request_id: string;
}

export interface ErrorPayload {
  code: string;
  message: string;
  // True when the turn cannot continue.
  fatal?: boolean;
}

export interface StatusPayload {
  state: "connecting" | "connected" | "disconnected" | "busy";
}

export type TurnStartPayload = Record<string, never>;

export interface TurnEndPayload {
  reason: "complete" | "cancelled" | "error";
}

export type CancelPayload = Record<string, never>;

export interface UserMessagePayload {
  text: string;
  // Python side is list[dict[str, Any]], so attachment entries are not
  // structurally constrained by the protocol.
  attachments?: Array<Record<string, unknown>>;
}

export interface ContextUpdatePayload {
  app: string;
  window: string;
  // Serialized as null (not omitted) when unset, because the Python payload
  // defaults it to None and to_dict() emits every field.
  project?: string | null;
}

export interface ShellEventBase {
  id: string;
  harness_id: string;
  conversation_id: string;
  timestamp: number;
  // Monotonic per-process sequence; 0 for events persisted before seq existed.
  seq?: number;
}

// Discriminated union: `type` determines the payload shape.
export type ShellEvent = ShellEventBase &
  (
    | { type: "message_chunk"; payload: MessageChunkPayload }
    | { type: "message_complete"; payload: MessageCompletePayload }
    | { type: "tool_call_start"; payload: ToolCallStartPayload }
    | { type: "tool_call_end"; payload: ToolCallEndPayload }
    | { type: "thinking_block"; payload: ThinkingBlockPayload }
    | { type: "approval_request"; payload: ApprovalRequestPayload }
    | { type: "approval_response"; payload: ApprovalResponsePayload }
    | { type: "error"; payload: ErrorPayload }
    | { type: "status"; payload: StatusPayload }
    | { type: "turn_start"; payload: TurnStartPayload }
    | { type: "turn_end"; payload: TurnEndPayload }
    | { type: "user_message"; payload: UserMessagePayload }
    | { type: "context_update"; payload: ContextUpdatePayload }
    | { type: "cancel"; payload: CancelPayload }
  );
