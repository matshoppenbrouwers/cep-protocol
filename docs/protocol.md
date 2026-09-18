# Common Event Protocol: reference

> **Disclaimer:** any harness named here (Hermes, OpenClaw) is unaffiliated third-party interop. This project is not associated with, endorsed by, or supported by their authors; it simply speaks their public APIs.

The Common Event Protocol (CEP) is a transport-agnostic event contract between a
desktop shell and an AI agent harness. A shell renders one event shape; each harness
is reached through an adapter that translates that harness's native transport into
CEP events.

**Architecture**: Shell ↔ Protocol Runtime ↔ Adapter Registry ↔ Harness Adapters ↔ Agent harnesses

This document describes the protocol as it was when the work stopped. It is a record,
not a specification under active development. See
[`adr-001-why-cep.md`](adr-001-why-cep.md) for why it looks the way it does.

---

## Protocol layer

### `cep/types.py`: event type system

**EventType** enum (14 event types):

| Event | Meaning |
|---|---|
| `MESSAGE_CHUNK` | Streaming text fragment |
| `MESSAGE_COMPLETE` | Full message, after streaming ends |
| `TOOL_CALL_START` | Tool invocation begins |
| `TOOL_CALL_END` | Tool invocation completes (success/failure) |
| `THINKING_BLOCK` | Agent thinking/reasoning text |
| `APPROVAL_REQUEST` | Permission request for a risky action |
| `APPROVAL_RESPONSE` | User's approval or denial |
| `ERROR` | Error from harness or adapter (`fatal` flag for turn-ending errors) |
| `STATUS` | Connection/session status change |
| `TURN_START` | Harness accepted a user message and began a turn |
| `TURN_END` | Turn finished (`reason`: complete/cancelled/error); harness idle |
| `USER_MESSAGE` | User input to send to the harness |
| `CONTEXT_UPDATE` | Desktop context change notification |
| `CANCEL` | Shell asks the harness to stop the running turn, best-effort |

**ShellEvent** dataclass:

- `type: EventType`: event kind
- `harness_id: str`: which adapter produced this event
- `conversation_id: str`: session/conversation scope
- `payload: Payload`: type-specific payload (union of 14 frozen dataclasses)
- `id: str`: `evt-` followed by 12 hex characters of a UUID4, auto-generated
- `timestamp: int`: Unix epoch milliseconds, auto-generated
- `seq: int`: process-wide monotonic sequence. Millisecond timestamps collide under
  streaming, so ordered replay sorts by `(timestamp, seq)`; `seq` is the tie-breaker
  that keeps same-millisecond chunks in emission order, and it resets each process.

**Payload types**: `MessageChunkPayload`, `MessageCompletePayload`,
`ToolCallStartPayload`, `ToolCallEndPayload`, `ThinkingBlockPayload`,
`ApprovalRequestPayload`, `ApprovalResponsePayload`, `ErrorPayload`, `StatusPayload`,
`TurnStartPayload`, `TurnEndPayload`, `CancelPayload`, `UserMessagePayload`,
`ContextUpdatePayload`.

**Serialization**: `ShellEvent.to_dict()` / `ShellEvent.from_dict()` for JSON-RPC or
any JSON transport. A payload-shape mismatch on deserialization logs a warning rather
than failing silently.

**Approval correlation**: `ApprovalRequestPayload.request_id` is the explicit
correlation key. Adapters populate it and mirror it onto `ShellEvent.id`; the
`APPROVAL_RESPONSE` echoes it in `ApprovalResponsePayload.request_id`. Where a harness
runs tools itself and can only report an approval after the fact, it sets
`risk="advisory"`: informational, not a gate.

### `cep/runtime.py`: event dispatcher

`ProtocolRuntime` is a pub/sub event bus:

- `subscribe(handler, event_types)`: register a handler with an optional type filter;
  returns a subscription id
- `unsubscribe(sub_id)`: remove a subscription
- `dispatch(event)`: fan out to all matching subscribers

---

## Adapter layer

### `cep/adapter.py`: the contract

**HarnessAdapter** ABC, a 6-method integration surface:

- `id: str` (property): unique adapter identifier
- `name: str` (property): human-readable display name
- `connect(config)`: establish a connection
- `disconnect()`: close it
- `send(event)`: send a `ShellEvent` to the harness
- `on_event(handler)`: register a callback for incoming events
- `health_check()`: non-blocking connectivity check

Helper methods for subclasses: `_emit_status(state, conversation_id)`,
`_emit_error(message, code, conversation_id, fatal)`,
`_emit_turn_start(conversation_id)`, `_emit_turn_end(conversation_id, reason)`.

**HarnessConfig** dataclass: `harness_id`, `name`, `host`, `port`, `auth_token`,
`base_url`, an `extra` dict, the runtime fields `binary_path` and `version`, and an
optional active `AgentProfile`.

**HealthStatus** dataclass: `connected`, `harness_id`, `latency_ms`, `error`.

**AgentProfile** dataclass (`profile_id`, `harness_id`, `name`, `working_dir`, `model`,
`system_prompt`, `permissions`, `created_at`). A profile is one named agent layered on
one installed runtime; several profiles can target the same runtime. The adapter
applies the active profile at connect/send time according to what the harness can
actually honour; a field the harness cannot apply is reported as unsupported rather
than silently dropped.

### `cep/registry.py`: adapter management

`AdapterRegistry`:

- `register(adapter)` / `unregister(adapter_id)`: add or remove adapters
- `set_active(adapter_id)`: switch the active harness
- `active` / `active_id`: the current active adapter
- `list_adapters()`: `dict` of id → name
- `get(adapter_id)`: retrieve a specific adapter

At most one adapter is active at a time. The registry starts with none active, and
unregistering the active adapter clears the selection.

---

## Adapters that existed

Three adapters were ever written against this contract: an in-process adapter inside
the desktop shell itself, the Hermes adapter (shipped here), and an OpenClaw adapter.
There was never a Claude Code or a Codex adapter. Only the Hermes adapter is published
in this repository; it is the legible reference implementation, and shipping one
avoids the question of which adapter is canonical.

### `cep/adapters/hermes.py`: SSE/HTTP

Connects to Hermes Agent's OpenAI-compatible API server (default port 8642):

- SSE streaming via `POST /v1/chat/completions`
- `_parse_sse_chunk()` translates streaming JSON into `ShellEvent`s
- `_consume_sse_stream()` is a buffered line-based SSE reader
- Health: `GET /health`, unauthenticated, returns `{"status":"ok"}`
- Auth: bearer token in the `Authorization` header

**Verified against a live harness, 2026-07-17.** The API server is **off by default**.
It is hosted by the `hermes gateway` process and needs `API_SERVER_ENABLED=true` plus a
generated `API_SERVER_KEY` in the harness `.env` (`~/.hermes/.env` on POSIX, while
Windows reads its config from `%LOCALAPPDATA%\hermes` under `HERMES_HOME`). Installing
Hermes is not enough; the config step has to run before first launch. The port is set
by `API_SERVER_PORT` in that same `.env`.

Hermes executes tools server-side, so approvals it reports are post-hoc and advisory.
The adapter keeps a hard connect-time gate: risky tools must be explicitly opted in, or
`connect()` refuses with `RiskyToolsGateError`, a `ConfigurationError` subclass defined
in `cep/adapters/hermes.py`.

Profile mapping, as verified against the Hermes build tested on 17 July 2026: the adapter
sends a per-request `model` and a profile-scoped `X-Hermes-Session-Id`, giving one isolated
session per agent profile. Current Hermes may require configuration to accept a bare model
override, and documents custom `hermes.tool.progress` events this adapter does not read, so
treat this as a record of one tested revision rather than a live compatibility claim.

### The OpenClaw adapter (not shipped here)

Recorded for completeness. This section describes **what the CEP adapter used**, not the
full capability surface of OpenClaw Gateway. The adapter spoke only the WebSocket protocol
at `ws://localhost:18789`. OpenClaw also exposes an HTTP REST API, including `/health`,
`/healthz` and OpenAI-compatible routes, which the adapter did not use. Everything below
is the WebSocket path as the adapter exercised it against gateway 2026.7.1, and the
limitations listed are the adapter's, not necessarily the gateway's:

- custom JSON framing, `{type: "req" | "res" | "event"}`
- a **client-initiated `connect` request** carrying scopes
  `["operator.read", "operator.write"]` (protocol version 4). Without scopes, every
  method returns a missing-scope error. Approval resolution needs a further
  `operator.approvals` scope. Auth is in-band via the `connect` params (`auth.token`),
  not an HTTP header; the server also opens with a `connect.challenge` frame.
- the server issues a `sessionKey`; messages go out via `chat.send`, streaming arrives
  as pushed `chat` events, terminal on `state: "final"`
- `sessions.abort` cancels; `exec.approval.request` / `exec.approval.resolve` carry
  approvals
- the adapter took health from the WS `status` method (requires `operator.read`); it did
  not use the gateway's HTTP `/health` and `/healthz` routes
- launch it in the foreground (`openclaw gateway run --port <port>`), never with
  `--install-daemon`, which registers OpenClaw's own OS service
- profile mapping: through the WebSocket methods the adapter used, it could isolate
  sessions per profile and nothing else, so working directory, model, system prompt and
  permissions were reported unsupported. That was a limit of the adapter's chosen
  surface; the gateway does support session model overrides by other routes.

---

## TypeScript types

`typescript/protocol.ts` mirrors the Python types for a TypeScript client.
`ShellEvent` is a **discriminated union** keyed on `type`, so payload shapes are
compile-checked at the call site. Harness identity is a plain string: there is no
hardcoded union of harness names and no table of default ports, so a new harness id
does not break a consumer's build.
