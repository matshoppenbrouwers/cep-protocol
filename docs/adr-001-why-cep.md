# ADR-001: Common Event Protocol and adapter layer

**Date**: 2026-06-05
**Status**: Accepted, then frozen. The application this was built for is discontinued;
the protocol is published as a record, not as a proposed standard.

---

## Context

The originating product began as a single-agent desktop app: a React UI talking to one
in-process ReAct agent over a direct WebSocket path. The direction it moved in was to
become a universal desktop shell for *any* agent harness, which meant separating three
layers:

- **Model**: the LLM provider
- **Harness**: the agent orchestrator, whether in-process or an external one such as
  Hermes or OpenClaw
- **Shell**: the desktop application: native UI, conversation history, context
  awareness, harness switching, approval UX

Each harness speaks a different transport and a different event shape: in-process
Python events, Hermes SSE over HTTP, OpenClaw WebSocket. Wiring the UI directly to each
one would fork the UI per harness and leak transport details into the shell. What was
needed was one contract the shell renders against regardless of which harness is
active, plus a single place conversation history lives so that memory and search work
across harnesses.

Constraints: keep the shipped single-agent chat working throughout, so no big-bang
cutover; do not break the existing approval and permission model; stay
transport-agnostic, so a new harness is a new adapter rather than a UI rewrite.

---

## Decision

Introduce a **Common Event Protocol (CEP)** and an **adapter layer**.

1. **One event contract.** `ShellEvent` with typed payloads and `to_dict` /
   `from_dict` for JSON transport. The UI and the history writer render against this
   contract and nothing else. The protocol shipped with 11 event types and ended at 14;
   `TURN_START`, `TURN_END` and `CANCEL` were added once it became clear the shell
   needed to know when a harness was busy and needed a way to interrupt it.

2. **Adapter registry.** Every harness implements the `HarnessAdapter` ABC, a
   contract of seven abstract members, and registers with `AdapterRegistry`. At most one
   adapter is active at a time, switched from the UI.

3. **Shadow history.** Every protocol event was persisted to SQLite regardless of which
   harness produced it, giving cross-harness conversation memory and search. That
   component is not part of this repository; it was tied to the application's own
   storage layer.

4. **Approval correlation.** An `APPROVAL_RESPONSE` is linked to its
   `APPROVAL_REQUEST` by an explicit `request_id`, mirrored onto the request event's
   own id. See [`protocol.md`](protocol.md).

5. **Replace, but coexist first.** The protocol path became the live transport for
   normal chat turns while slash-command and `@mention` turns stayed on the legacy
   gateway during the transition, with the legacy path marked for retirement once the
   protocol path covered those turns too. It never got there.

---

## Consequences

### Positive

- Adding a harness is an adapter plus a registration, not a UI change; the shell
  renders one event shape.
- Cross-harness conversation memory and search, because one history writer sees every
  event.
- A clean Model / Harness / Shell separation, with harness switching as a first-class
  UI affordance.

### Negative

- **Two chat-history stores during the transition**: one for legacy slash and
  `@mention` turns, one for protocol turns. This is a source-of-truth split, it was
  tracked as a known risk, and convergence was never delivered.
- **Two live code paths**, protocol and legacy gateway, for as long as the gateway
  survived: more surface to keep correct, and every fix had to be considered twice.
- The protocol message boundary is a **new untrusted input surface**. Validation was
  added later, after a security review flagged it, rather than designed in.
- The event set grew under pressure. Turn lifecycle and cancellation were not in the
  original 11 events and had to be retrofitted once real harnesses were connected,
  which meant a version boundary in a protocol that had no versioning story.

### Neutral

- The external adapters were implemented but not registered by default in the shipping
  build; they were dormant behind a setting. A harness that executes tools server-side
  can only report approvals after the fact, so its approvals are advisory and its
  adapter refuses risky tools unless explicitly opted in, pending a pre-execution
  permission round-trip that was never built.
- The in-process agent's own event types remained; the in-process adapter translated
  them into protocol events.

---

## Alternatives considered

### Alternative 1: the UI talks to each harness directly

**Description**: add harness-specific client code in the React UI for each transport.

**Pros**: no new abstraction, and the fastest route to the first extra harness.

**Cons**: the UI forks per harness, and transport details leak into the shell. There is
no shared history, so memory and search cannot span harnesses.

**Why not chosen**: it defeats the universal-shell goal and scales badly past one extra
harness.

### Alternative 2: adopt an existing agent wire format as the internal contract

**Description**: use a vendor stream format, such as OpenAI-compatible SSE, as the
shell's internal event model.

**Pros**: reuses a known schema, and some harnesses already emit it.

**Cons**: vendor formats do not model desktop concerns (approval round-trips, desktop
context updates, harness identity) cleanly, so they would have to be extended anyway.

**Why not chosen**: a small purpose-built contract fits the shell's needs with less
impedance, and adapters translate vendor formats into it. The Hermes adapter does
exactly this for SSE.

### Alternative 3: big-bang cutover, replacing the gateway immediately

**Why not chosen**: it risked breaking shipped chat. The coexistence path let the
protocol prove itself on normal turns while slash and `@mention` turns stayed on the
gateway that already worked. The cost of that choice is the first two negative
consequences above.

---

## In hindsight

Zed's Agent Client Protocol (ACP) reached this layer and won it. ACP was announced on
27 August 2025; CEP's protocol core was first committed on 5 April 2026 and was designed
without reference to it, so the two converged independently rather than CEP arriving first.
CEP then went nowhere, because a protocol is adopted for its ecosystem rather than its design. Anyone looking for a live standard
at this layer should be looking at ACP. What is worth reading here is the shape two
independent attempts converged on, and the list of things this one got wrong.
