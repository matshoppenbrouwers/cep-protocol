# Changelog

## v0.1.1 - 2026-09-19

A documentation release. The code is identical to `v0.1.0`; the quickstart in the README
did not run as written, and a pinned install deserves a copy that does.

Both defects were found by cloning the published repository and following the quickstart
verbatim, which is the check that should have run before `v0.1.0`.

### Fixed

- The install line offered for "reading and running the tests" was `.[hermes]`, which
  carries only `aiohttp`. `pytest` and `pytest-asyncio` are in the `dev` extra, so the
  suite could not run. It is now `.[hermes,dev]`.
- The example subscriber read `event.payload.text` for every event it received, including
  `ERROR`, whose payload carries `code` and `message` and no `text`. The Hermes API server
  is off by default, so a first run ends in a connection error and the example then raised
  `AttributeError` inside the handler. `ProtocolRuntime.dispatch` caught and logged it, so
  the script exited 0 having printed a traceback and no error message. The example now
  branches on event type.

## v0.1.0 - 2026-09-18

The archival release of the Common Event Protocol, extracted from CommandLane, a
discontinued desktop agent shell. See the no-maintenance notice in the README.

### Extracted

- Protocol core: 14-event taxonomy, frozen payload dataclasses and the `ShellEvent`
  envelope with a millisecond timestamp and a process-monotonic `seq` tie-breaker
- `ProtocolRuntime` pub/sub dispatcher
- `HarnessAdapter` ABC (five abstract methods, two abstract properties) and
  `AdapterRegistry`
- Hermes reference adapter over SSE/HTTP
- The TypeScript type slice mirroring the protocol
- Ported tests, the protocol reference and the originating ADR

Three adapters existed historically: CommandLane in-process, Hermes and OpenClaw.
Only Hermes is published here.

### Corrected before release

A pre-publication review found claims the code did not support:

- The README and ADR said CEP preceded ACP by twelve months. ACP was announced
  2025-08-27; CEP's protocol core was first committed 2026-04-05. Reframed as
  independent convergence, which is what the evidence supports.
- The documented forward-compatibility fallback does not exist: `from_dict()` raises
  `ValueError` on an unknown event type.
- The OpenClaw section asserted that gateway v2026.7.1 is WebSocket-only with no REST
  API or HTTP health path. It serves `/health`, `/healthz` and OpenAI-compatible
  routes. Rewritten to describe the adapter's own surface.
- The adapter contract is seven abstract members, not six methods.

### Fixed

- A UTF-8 character split across network chunks raised `UnicodeDecodeError`
- `TOOL_CALL_END` asserted `success=True` for tool outcomes never observed; Hermes
  executes tools server-side and returns no result, so the event is no longer emitted
- SSE error envelopes were ignored, and a stream ending without a terminal marker was
  promoted to `MESSAGE_COMPLETE`
- `asyncio.CancelledError` escaped the turn handler, leaving subscribers without a
  `turn_end`
- Overlapping sends on one conversation broke cancel tracking
- `allow_risky_tools` accepted any truthy value, so the string `"false"` opened the gate
