# CEP: Common Event Protocol

CEP is a 14-event protocol for the traffic between a shell (the UI a person types into) and a
harness (the agent runtime that answers), plus an adapter contract of seven abstract members that
translates a harness's native transport (WebSocket, SSE, in-process calls) into those events.

Extracted from a discontinued desktop application in 2026 and frozen. The Python core
(`cep.types`, `cep.runtime`, `cep.adapter`, `cep.registry`) is standard-library only. A
TypeScript slice of the same event union lives in `typescript/`.

```
shell  ──ShellEvent──▶  ProtocolRuntime  ──▶  AdapterRegistry  ──▶  HarnessAdapter  ──▶  harness
       ◀─ShellEvent───                    ◀──                   ◀──
```

Terms used throughout, and defined once here:

- Shell: the desktop UI. It owns conversation history, context awareness and the approval prompt.
- Harness: the agent orchestrator that runs the loop and calls tools. It may be in-process or a
  separate program reached over HTTP or WebSocket.
- Adapter: one class per harness, translating that harness's native transport into `ShellEvent`s.
- Model: the LLM provider underneath the harness. CEP never touches this layer.

---

## What CEP is for

A desktop shell that wants to drive more than one agent harness has to render each harness's
output somehow. Wiring the UI to each transport directly forks the UI per harness and leaks
framing details into the view layer. CEP is the alternative: one event shape the UI renders
against, and one adapter per harness behind it, so adding a harness is an adapter and a
registration rather than a UI change.

This repository is a record of a design that was built and used, not a standard anyone is being
asked to adopt. If you are wiring an agent runtime to an editor or terminal today, use
[ACP](https://github.com/zed-industries/agent-client-protocol), which is the live standard at
this layer and has the ecosystem to match. What is worth reading here is the shape a second,
independent attempt at the same problem arrived at, and the list of things it got wrong.

On chronology, so no one has to guess: ACP was announced on 27 August 2025, and CEP's protocol
core was first committed on 5 April 2026, without reference to it. The two converged; CEP did not
come first, and this repository claims no precedence.

---

## Event taxonomy

Fourteen event types, defined in `cep/types.py` as `EventType`. Every one travels in the same
`ShellEvent` envelope: `id`, `type`, `harness_id`, `conversation_id`, `timestamp` (ms), `seq`,
`payload`. The `seq` field is a process-monotonic counter that tie-breaks events created within
the same millisecond, so `(timestamp, seq)` is a total order for replay within a process
run; `seq` resets when the process does.

### Harness to shell (10 events)

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

### Shell to harness (4 events)

| Event | Payload | Meaning |
|---|---|---|
| `user_message` | `text`, `attachments` | What the person typed |
| `approval_response` | `approved`, `request_id` | The answer to an `approval_request` |
| `context_update` | `app`, `window`, `project` | Ambient context about what the person is looking at |
| `cancel` | - | Stop the running turn |

Payloads are frozen, slotted dataclasses. `ShellEvent.to_dict()` / `from_dict()` round-trip
through plain JSON and rebuild the typed payload on the way back, falling back to a raw dict when
a *known* event's payload shape does not match its dataclass. Unknown event types are not
tolerated: `from_dict()` raises `ValueError`, so a shell has to be upgraded before it can read an
event type a newer harness introduces. CEP has no forward-compatibility story and no version
negotiation, which is the single clearest thing it got wrong.

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
| `send` | `async (event: ShellEvent) -> None` | Push a shell-to-harness event across |
| `on_event` | `(handler: EventHandler) -> None` | Register the callback for harness-to-shell events |
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

This repository ships one reference adapter: Hermes, over SSE/HTTP against an
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

Three adapters existed historically: an in-process adapter for the host application's own
agent, the Hermes adapter over SSE/HTTP, and an OpenClaw adapter over WebSocket with a
real round-trip approval gate. Only Hermes ships here; the in-process adapter was inseparable
from the application, and shipping two external adapters would only raise the question of which
is canonical. Those three are the complete historical list; CEP was never wired to any other
harness, and any claim of broader coverage is wrong.

The protocol core is unchanged from its production form apart from import paths, one locally
redefined exception class, the `AgentProfile` dataclass inlined into `cep/adapter.py` from
its own module, stripped internal planning comments, and packaging metadata. The ported tests run
green with no dependency on the original codebase.

This repository is not maintained. It is a frozen artifact, published as a reference and a
record of a design. `v0.1.0` is the archival release of the extracted code; no further releases
are planned. Issues and pull requests will not be reviewed, and no support is offered. Fork it
freely; that is what the licence is for.

---

## Questions

### What is the Common Event Protocol?

A 14-event contract between a desktop shell and an AI agent harness, with an adapter layer that
translates each harness's native transport into those events. It covers streaming text, tool
calls, an out-of-band approval round-trip, turn boundaries and cancellation.

### What problem does CEP solve?

One UI driving several agent harnesses. Without a shared event shape, the UI forks per harness
and transport details leak into the view layer; with one, the shell renders a single event log
and a new harness is one adapter class.

### Is CEP maintained, and should I adopt it?

No, and no. It is frozen at `v0.1.0` and published as a record. For live work at this layer,
ACP is the standard with an ecosystem behind it. Read CEP for the design and for the failure
modes documented in the ADR.

### What does an adapter have to implement?

Seven abstract members on `HarnessAdapter`: the `id` and `name` properties, and `connect`,
`disconnect`, `send`, `on_event` and `health_check`. The base class supplies status, error and
turn-lifecycle emission.

### Does CEP handle permissions and approvals?

Yes, through an `approval_request` / `approval_response` pair correlated by an explicit
`request_id`. Where a harness executes tools itself and can only report an approval after the
fact, the request carries `risk="advisory"` and gates nothing; the Hermes adapter refuses to
connect for risky tools unless a caller opts in explicitly.

### Does CEP have versioning or forward compatibility?

No. `ShellEvent.from_dict()` raises `ValueError` on an unknown event type, so a shell cannot read
events from a newer harness. The event set grew from 11 types to 14 during development with no
version boundary, which is recorded as a negative consequence in the ADR.

### What is the difference between a harness and a model?

The model is the LLM provider. The harness is the orchestrator around it: the agent loop, tool
execution and session state. CEP sits between the shell and the harness and never sees the model.

### Is there a TypeScript implementation?

`typescript/protocol.ts` mirrors the Python event union as a discriminated union keyed on `type`,
so payload shapes are compile-checked at the call site. It is types only; there is no TypeScript
runtime or adapter.

---

## Further reading

- [`docs/protocol.md`](docs/protocol.md): the full event reference, the adapter contract,
  and the verified per-harness notes for Hermes and OpenClaw.
- [`docs/adr-001-why-cep.md`](docs/adr-001-why-cep.md): the decision record, the three
  rejected alternatives and the negative consequences, recorded without varnish.

---

## Licence

MIT. See [LICENSE](LICENSE).
