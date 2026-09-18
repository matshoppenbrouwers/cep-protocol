# Changelog

## v0.1.0 — 2026-09-18

The archival release of the Common Event Protocol, extracted from CommandLane, a
discontinued desktop agent shell. This is the only release planned; see the
no-maintenance notice in the README.

### Extracted

- Protocol core: 14-event taxonomy, frozen payload dataclasses and the `ShellEvent`
  envelope with a millisecond timestamp and a process-monotonic `seq` tie-breaker
- `ProtocolRuntime` pub/sub dispatcher
- `HarnessAdapter` ABC (five abstract methods, two abstract properties) and
  `AdapterRegistry`
- Hermes reference adapter over SSE/HTTP
- The TypeScript type slice mirroring the protocol
- Ported tests, the protocol reference and the originating ADR

Three adapters existed historically — CommandLane in-process, Hermes and OpenClaw.
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
