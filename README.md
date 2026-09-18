# CEP: Common Event Protocol

A 14-event protocol for the traffic between a **shell** (the UI a person types into) and a
**harness** (the agent runtime that answers), plus an adapter contract of seven abstract members that
translates a harness's native transport (WebSocket, SSE, in-process calls) into those events.

Extracted from a discontinued desktop application in 2026 and frozen. The Python core
(`cep.types`, `cep.runtime`, `cep.adapter`, `cep.registry`) is standard-library only. A
TypeScript slice of the same event union lives in `typescript/`.

```
shell  ──ShellEvent──▶  ProtocolRuntime  ──▶  AdapterRegistry  ──▶  HarnessAdapter  ──▶  harness
       ◀─ShellEvent───                    ◀──                   ◀──
```

---

## Status: a road taken, not a proposed standard

ACP won this layer. Zed's [Agent Client Protocol](https://github.com/zed-industries/agent-client-protocol)
is the interface that editors and terminals actually build against: ~3.8k stars, a JetBrains
implementation, an ACP Registry, and a native agent pane in Microsoft's Intelligent Terminal. If
you are wiring an agent runtime to a client today, use ACP. This repository is not competing with
it and is not asking anyone to adopt anything.

What makes CEP worth reading is that it converged on a near-identical shape independently, from a
different starting problem. The chronology is the other way round from what convergence might
suggest: Zed announced ACP on 27 August 2025, and CEP's protocol core was first committed on
5 April 2026, about seven months later, without reference to it. So this is convergent evolution
and not precedence, and certainly not a moat: two designs pushed into the same form by the same
constraints: streaming text, tool calls that must be shown before they finish, an out-of-band
permission ask, and a turn boundary the UI can trust. The two protocols disagree mostly about transport and framing, not about what the
events are.

### CEP events vs. ACP `session/update`

CEP is a flat, typed event envelope in both directions. ACP is JSON-RPC: the streaming half is
one `session/update` notification carrying a discriminated `sessionUpdate` variant, and the
request/response half is separate methods.

| CEP event | Direction | Nearest ACP construct |
|---|---|---|
| `message_chunk` | harness → shell | `session/update` → `agent_message_chunk` |
| `message_complete` | harness → shell | (implicit; ACP ends the stream with the `session/prompt` response) |
| `thinking_block` | harness → shell | `session/update` → `agent_thought_chunk` |
| `tool_call_start` | harness → shell | `session/update` → `tool_call` (status `pending`/`in_progress`) |
| `tool_call_end` | harness → shell | `session/update` → `tool_call_update` (status `completed`/`failed`) |
| `approval_request` | harness → shell | `session/request_permission` (a request, not a notification) |
| `approval_response` | shell → harness | the `session/request_permission` result |
| `error` | harness → shell | JSON-RPC error object on the enclosing call |
| `status` | harness → shell | (no direct equivalent; ACP infers liveness from the connection) |
| `turn_start` | harness → shell | (implicit; the `session/prompt` call itself) |
| `turn_end` | harness → shell | the `session/prompt` response `stopReason` |
| `user_message` | shell → harness | `session/prompt` |
| `context_update` | shell → harness | (no direct equivalent; closest is prompt content blocks / `@`-mentions) |
| `cancel` | shell → harness | `session/cancel` |

The differences are honest ones. ACP models a turn as a call whose return value *is* the turn
boundary; CEP models it as two explicit events, which is easier for a UI that renders from a
single event log and harder for a caller that wants a promise. ACP has no `status` event because
its transport is a live stdio connection; CEP needed one because adapters sat behind HTTP. CEP's
`context_update` (ambient desktop context pushed from the shell, unprompted) is the one event
with no ACP counterpart at all, and the one place the two designs genuinely diverge.

ACP also covers ground CEP never did: `initialize` capability negotiation, session load/resume,
filesystem and terminal methods delegated back to the client, slash commands, and modes. CEP
assumed the shell owned all of that.

---

## Event taxonomy

Fourteen event types, defined in `cep/types.py` as `EventType`. Every one travels in the same
`ShellEvent` envelope: `id`, `type`, `harness_id`, `conversation_id`, `timestamp` (ms), `seq`,
`payload`. The `seq` field is a process-monotonic counter that tie-breaks events created within
the same millisecond, so `(timestamp, seq)` is a total order for replay within a process
run; `seq` resets when the process does.

### Harness → shell (10)

| Event | Payload | Meaning |
|---|---|---|
| `message_chunk` | `text`, `done` | A streamed fragment of assistant text |
| `message_complete` | `text`, `role` | The finished message, whole |
| `tool_call_start` | `tool_name`, `args` | A tool invocation began |
| `tool_call_end` | `tool_name`, `result`, `success` | That invocation finished |
| `thinking_block` | `text` | Reasoning the harness chose to expose |
| `approval_request` | `action`, `description`, `risk`, `request_id` | The shell must decide before (or, where the harness cannot gate, about) an action. `risk` is `low` / `medium` / `high` / `advisory`, where `advisory` means the action already ran and this is a notice, not a gate |
| `error` | `code`, `message`, `fatal` | Something failed; `fatal` means the turn cannot continue |
| `status` | `state` | `connecting` / `connected` / `disconnected` / `busy` |
| `turn_start` | - | The harness accepted a user message and began work |
| `turn_end` | `reason` | `complete` / `cancelled` / `error`; the harness is idle again |

### Shell → harness (4)

| Event | Payload | Meaning |
|---|---|---|
| `user_message` | `text`, `attachments` | What the person typed |
| `approval_response` | `approved`, `request_id` | The answer to an `approval_request` |
| `context_update` | `app`, `window`, `project` | Ambient context about what the person is looking at |
| `cancel` | - | Stop the running turn |

Payloads are frozen, slotted dataclasses. `ShellEvent.to_dict()` / `from_dict()` round-trip
through plain JSON and rebuild the typed payload on the way back, falling back to a raw dict when
a *known* event's payload shape does not match its dataclass. Unknown event *types* are not
tolerated: `from_dict()` raises `ValueError`, so a shell has to be upgraded before it can read an
event type a newer harness introduces. CEP has no forward-compatibility story, which is one of
the places ACP is simply better designed.

---

## Adapter contract

A harness integration is one class extending `HarnessAdapter` (`cep/adapter.py`). The entire
required surface is seven abstract members, grouped as six operations: connect, disconnect,
send, receive, health-check, and identify (the `id` / `name` property pair).

| Member | Signature | Responsibility |
|---|---|---|
| `id` | `property -> str` | Stable identifier for this adapter type |
| `name` | `property -> str` | Human-readable name for display |
| `connect` | `async (config: HarnessConfig) -> None` | Open the transport |
| `disconnect` | `async () -> None` | Close it |
| `send` | `async (event: ShellEvent) -> None` | Push a shell→harness event across |
| `on_event` | `(handler: EventHandler) -> None` | Register the callback for harness→shell events |
| `health_check` | `async () -> HealthStatus` | Non-blocking connectivity probe |

Concretely that is five abstract methods plus two abstract properties: seven
`@abstractmethod` declarations in `cep/adapter.py`.

The base class supplies the rest: `_emit_status`, `_emit_error`, `_emit_turn_start` and
`_emit_turn_end` build correctly-shaped envelopes so adapters never construct turn bookkeeping by
hand. Two optional hooks cover real-world variation; `has_active_turn(conversation_id)`, which
async-transport adapters override so the shell knows events are still arriving after `send()`
returns, and `unsupported_profile_fields`, a frozenset naming `AgentProfile` fields the harness
cannot honor so they are flagged rather than silently dropped.

`AdapterRegistry` (`cep/registry.py`) holds installed adapters and the active selection, keyed on
a runtime id so two instances of the same harness type can coexist.

---

## Quickstart: the Hermes reference adapter

This repository ships *one* reference adapter: Hermes, over SSE/HTTP against an
OpenAI-compatible endpoint. It is the legible one; the whole translation from a token stream to
CEP events is readable in a single file, `cep/adapters/hermes.py`.

```bash
# From a clone, for reading and running the tests:
pip install -e ".[hermes]"

# Or pinned to the archival release:
pip install "cep[hermes] @ git+https://github.com/matshoppenbrouwers/cep-protocol@v0.1.0"
```

```python
import asyncio
import os

from cep import EventType, HarnessConfig, ProtocolRuntime, ShellEvent
from cep.adapters import HermesAdapter
from cep.types import UserMessagePayload

runtime = ProtocolRuntime()
runtime.subscribe(
    lambda event: print(event.payload.text, end="", flush=True),
    event_types={EventType.MESSAGE_CHUNK, EventType.ERROR},
)

adapter = HermesAdapter()
adapter.on_event(runtime.dispatch)


async def main() -> None:
    await adapter.connect(
        HarnessConfig(
            harness_id="hermes",
            name="Hermes Agent",
            port=8642,
            auth_token=os.environ["HERMES_API_KEY"],
            extra={"allow_risky_tools": True},
        )
    )
    await adapter.send(
        ShellEvent(
            type=EventType.USER_MESSAGE,
            harness_id="hermes",
            conversation_id="demo",
            payload=UserMessagePayload(text="Summarise the CEP event taxonomy."),
        )
    )
    await adapter.disconnect()


asyncio.run(main())
```

`allow_risky_tools` is a deliberate speed bump. Hermes runs tools server-side, so this adapter
can only observe a risky call after it has already happened; its `approval_request` carries
`risk="advisory"` and gates nothing. `connect()` refuses without the explicit opt-in rather than
letting a shell believe it has a permission gate it does not have.

**Disclaimer:** the Hermes integration is unaffiliated third-party interop. This project is not
associated with, endorsed by, or supported by the authors of Hermes; it simply speaks their
public HTTP API. The same goes for any other harness named in this repository.

---

## Provenance and maintenance

CEP was designed and used in production inside CommandLane, a discontinued desktop agent shell,
and is extracted here under MIT so the design outlives the application.

Three adapters existed historically: an **in-process adapter** for the host application's own
agent, the **Hermes** adapter over SSE/HTTP, and an **OpenClaw** adapter over WebSocket with a
real round-trip approval gate. Only Hermes ships here; the in-process adapter was inseparable
from the application, and shipping two external adapters would only raise the question of which
is canonical. Those three are the complete historical list; CEP was never wired to any other
harness, and any claim of broader coverage is wrong.

The protocol core is unchanged from its production form apart from import paths, one locally
redefined exception class, the `AgentProfile` dataclass inlined into `cep/adapter.py` from
its own module, stripped internal planning comments, and packaging metadata. The ported tests run green with no dependency
on the original codebase.

This repository is not maintained. It is a frozen artifact, published as a reference and a
record of a design. `v0.1.0` is the archival release of the extracted code; no further releases
are planned. Issues and pull requests will not be reviewed, and no support is offered. Fork it freely; that is what the licence is for. For live work at this
layer, go to [ACP](https://github.com/zed-industries/agent-client-protocol).

## Further reading

- [`docs/protocol.md`](docs/protocol.md): the full event reference, the adapter contract,
  and the verified per-harness notes for Hermes and OpenClaw.
- [`docs/adr-001-why-cep.md`](docs/adr-001-why-cep.md): the decision record, the three
  rejected alternatives and the negative consequences, recorded without varnish.

---

## Licence

MIT. See [LICENSE](LICENSE).
