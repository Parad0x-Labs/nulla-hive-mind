# INTERNAL HANDOVER EXTENDED

## Purpose

This document is the detailed internal reference for the current Decentralized NULLA codebase.

It exists to capture, in one place:

- what the system is,
- why it is shaped that way,
- where each major subsystem lives,
- how the major layers work together,
- what is implemented versus partial versus simulated,
- how integrations are meant to fit,
- and what still has to happen before broader release or larger-scale deployment.

This document is intentionally more detailed than the public-facing or operator-facing summaries.

For the deepest current audit view, use:

- `docs/INTERNAL_SYSTEM_MEGA_DOSSIER.md`

That file is the heaviest current internal reference and should be preferred for external technical audit or deep continuation work.

## 1. Honest Current State

Decentralized NULLA is currently a local-first distributed agent system with real product and runtime substance.

The current codebase already supports:

- standalone local operation,
- trusted LAN swarm orchestration,
- signed peer messaging,
- safe helper capsule execution boundaries,
- task decomposition and reassembly,
- local review and verdict logic,
- fraud and dispute scaffolding,
- content-addressed local storage,
- append-only event chaining,
- knowledge-presence metadata,
- a first meet-and-greet coordination service scaffold,
- and user-facing introspection of NULLA's own memory and mesh state.

Latest hardening delta in this repo state also includes:

- protocol-level `REPORT_ABUSE` validation and bounded abuse gossip forwarding,
- dedupe persistence for seen abuse reports,
- policy-driven PoW difficulty enforcement in capability advertisement validation,
- and SQLite lock-behavior hardening (`busy_timeout` and `synchronous=NORMAL` with WAL mode).
- stream-first oversized transfer with UDP fragmentation/reassembly fallback,
- optional meet TLS listener support and HTTPS replication trust options,
- optional mesh payload encryption via PSK-based AES-GCM,
- and Linux network-namespace sandbox isolation path with fail-closed policy mode.

The codebase does not yet honestly support these stronger claims:

- public trustless decentralized compute economy,
- hostile-world internet-scale swarm,
- final production payment settlement,
- or easy mass-distribution with one-click update discipline.

The correct current description is:

- serious local-first platform,
- serious LAN and friend-swarm prototype,
- credible architecture for further growth,
- and a codebase that is now in proof, deployment, and release-engineering territory rather than pure concept territory.

## 2. Design Principles

The system is shaped by a few rules that should remain stable.

### Local First

NULLA must remain useful on one device.

The swarm is an enhancement layer, not the reason the product exists.

### Keep Runtime State Out Of Handoffs

Live local runtime state should not be treated as source code.

That includes:

- `.nulla_local/`
- local signing keys
- populated local SQLite state

Sharing this repository with other machines should use sanitized code/config content, not live operator state.

### Metadata First, Payload On Demand

The swarm should advertise:

- presence,
- capability,
- manifests,
- holders,
- freshness,
- and routes.

It should not broadcast full content or sensitive user context by default.

### Hot Plane Separate From Content Plane

Mutable live index state should remain small and directly queryable.

Large payloads should move through chunked transport, CAS, and optionally Liquefy-backed packing.

### Payment And Proof Should Stay Async

Payments and receipts should not block the live coordination plane.

### Explicit Truth Labels

The codebase now tries to distinguish:

- implemented,
- partial,
- simulated,
- planned.

This distinction must continue because the project got into trouble earlier by blending those categories too aggressively.

## 3. Current Runtime Modes

### Standalone Local Mode

This is the base product mode.

A single local NULLA instance can:

- accept messy user input,
- normalize and interpret it,
- create a task record,
- classify the task,
- apply safety gating,
- build a local plan,
- render a response,
- store task state,
- write audit signals,
- and synthesize a durable local learning shard when appropriate.

This behavior lives primarily in:

- `apps/nulla_agent.py`
- `core/human_input_adapter.py`
- `core/task_router.py`
- `core/reasoning_engine.py`
- `core/feedback_engine.py`
- `core/shard_synthesizer.py`

### Trusted LAN Swarm Mode

This is the current real distributed runtime.

The daemon path can:

- advertise local capability,
- discover peers,
- receive task offers,
- claim helper work,
- execute helper capsules within boundaries,
- submit results,
- review or finalize outputs,
- and periodically broadcast presence and knowledge metadata.

This behavior lives primarily in:

- `apps/nulla_daemon.py`
- `network/assist_router.py`
- `network/transport.py`
- `network/protocol.py`
- `core/maintenance.py`
- `core/result_reviewer.py`
- `core/finalizer.py`

### Knowledge-Aware Swarm Mode

This mode is the current swarm-memory metadata layer.

It allows nodes to know:

- who is online,
- what knowledge shards are claimed,
- which holders exist,
- which versions exist,
- which claims are fresh,
- and where fetch routes point.

This behavior lives in:

- `core/knowledge_advertiser.py`
- `core/knowledge_registry.py`
- `core/knowledge_fetcher.py`
- `core/knowledge_replication.py`
- `network/knowledge_models.py`
- `network/knowledge_router.py`
- `network/presence_router.py`
- `storage/knowledge_index.py`
- `storage/knowledge_manifests.py`
- `storage/replica_table.py`

### Meet-And-Greet Coordination Mode

This is the early coordination service for multi-node swarm entry and hot metadata indexing.

It now includes:

- service schemas,
- a service facade,
- HTTP routing,
- meet-node registry state,
- snapshot and delta replication,
- and sync cursors.

This behavior lives in:

- `core/meet_and_greet_models.py`
- `core/meet_and_greet_service.py`
- `core/meet_and_greet_replication.py`
- `apps/meet_and_greet_server.py`
- `apps/meet_and_greet_node.py`
- `storage/meet_node_registry.py`
- `storage/payment_status.py`

Current safety posture:

- local meet nodes default to loopback binding,
- non-loopback deployment is expected to provide an explicit auth token,
- write requests are body-size capped,
- write traffic is rate-limited,
- and the sample 3-node config pack still contains placeholder public URLs and placeholder tokens that must be replaced before real deployment.

### Mobile Companion And Channel Access Mode

This is the intended pre-Git device-expansion mode.

The correct shape is:

- primary NULLA brain on a desktop, laptop, or stable server,
- phone as companion client and lightweight presence or summary mirror,
- and OpenClaw-style integrations as channel gateways rather than system-brain replacements.

This mode is compatible with the current architecture because:

- local-first behavior already exists,
- knowledge-presence metadata already exists,
- meet-and-greet already handles hot metadata,
- and the system already has early Telegram and Discord relay surfaces.

What does not exist yet is a finished native mobile product or hardened public channel stack.

Supporting docs:

- `docs/MOBILE_OPENCLAW_SUPPORT_ARCHITECTURE.md`
- `docs/MOBILE_CHANNEL_ROLLOUT_PLAN.md`
- `docs/MOBILE_CHANNEL_TEST_CHECKLIST.md`
- `docs/OVERNIGHT_SOAK_RUNBOOK.md`
- `ops/mobile_channel_preflight_report.py`
- `ops/overnight_readiness_report.py`
- `ops/morning_after_audit_report.py`

## 4. Architectural Split

The current architecture should be understood as five layers.

### Layer 1: User-Facing Local Intelligence

This layer exists on every device and is the actual product.

Its responsibilities:

- human input understanding,
- persona and tone,
- local task framing,
- local planning,
- local memory synthesis,
- safe local response generation,
- and user-facing introspection.

### Layer 2: Swarm Control And Knowledge Metadata

This layer handles:

- peer discovery,
- presence,
- capability advertisements,
- knowledge advertisements,
- holder maps,
- and fetch routes.

### Layer 3: Meet-And-Greet Hot Coordination Plane

This is a small shared infrastructure layer for:

- live presence leases,
- knowledge manifest metadata,
- node registry,
- payment state markers,
- and delta or snapshot coordination.

### Layer 4: Content And Archive Plane

This is where chunked payloads, CAS objects, archive bundles, and optional Liquefy packing belong.

### Layer 5: Async Proof And Payment Plane

This is where:

- DNA receipts,
- settlement evidence,
- historical proof artifacts,
- and optional economic sidecars belong.

These layers should not collapse into one all-purpose storage or transport system.

## 5. Directory-Level System Map

### `apps/`

This directory contains the runtime entry points.

#### `apps/nulla_agent.py`

Purpose:

- user-facing local agent runtime.

What it does:

- starts local policy and persona,
- adapts messy user input,
- creates task records,
- classifies tasks with interpretation context,
- triggers parent orchestration,
- requests relevant holders when local confidence is weak,
- builds local evidence and plans,
- applies a default safety gate,
- renders the response,
- evaluates outcome,
- and stores durable local learning shards.

Why it matters:

- this is the clearest expression of NULLA as a product rather than only infrastructure.

#### `apps/nulla_daemon.py`

Purpose:

- swarm-facing daemon runtime.

What it does:

- starts transport,
- registers local endpoints,
- generates local capability advertisement,
- syncs local learning shards into the knowledge index,
- performs NAT and relay classification,
- starts maintenance loops,
- broadcasts hello and knowledge advertisements,
- and runs the order-book-related helper loop.

Why it matters:

- this is the main bridge between local intelligence and distributed mesh behavior.

#### `apps/nulla_cli.py`

Purpose:

- local operator and developer interface.

What it does:

- starts runtimes,
- exposes local actions,
- and now includes the summary entry path for the user-facing memory and mesh report.

#### `apps/meet_and_greet_server.py`

Purpose:

- HTTP scaffold for the meet-and-greet hot coordination service.

What it does:

- exposes presence,
- knowledge,
- payment marker,
- snapshot,
- delta,
- cluster node,
- and health endpoints.

Why it matters:

- it turns the meet layer from abstract architecture into an actual service contract.

#### `apps/meet_and_greet_node.py`

Purpose:

- runtime wrapper for operating a meet node.

What it does:

- packages service and replication behavior into a dedicated coordinating node shape.

Why it matters:

- it is the start of shared swarm infrastructure that is separate from ordinary agents.

### `core/`

This directory contains most of the product logic and orchestration logic.

#### Identity, Persona, And User-Facing Behavior

- `identity_manager.py`
  - loads and manages the active persona.
- `agent_name_registry.py`
  - tracks or normalizes naming identity.
- `reasoning_engine.py`
  - builds plans and renders responses.
- `nulla_user_summary.py`
  - composes the user-facing memory and mesh summary report.

These modules make NULLA feel like a local intelligence rather than only a transport mesh.

#### Human Input Adaptation

- `input_normalizer.py`
  - normalizes messy input, shorthand, and typo patterns.
- `human_input_adapter.py`
  - reconstructs usable intent context from messy user text, tracks topics and references, and assigns understanding confidence.

Why this layer matters:

- users do not speak in clean API payloads,
- and NULLA has to survive broken grammar, abrupt references, and shorthand if it is going to feel usable.

#### Task Lifecycle, Routing, And Orchestration

- `task_router.py`
  - creates task records, redacts input, classifies tasks, initializes trace and state.
- `task_state_machine.py`
  - enforces explicit lifecycle transitions.
- `retry_policy.py`
  - defines retry rules.
- `timeout_policy.py`
  - defines timeout behavior.
- `trace_id.py`
  - manages trace identifiers.
- `parent_orchestrator.py`
  - manages the parent task flow and decomposition.
- `task_decomposer.py`
  - splits tasks into subtasks.
- `task_reassembler.py`
  - recombines accepted results.
- `assist_coordinator.py`
  - coordinates swarm assistance behavior.

Why this layer matters:

- it turns the system into a structured orchestration runtime instead of loose peer messaging.

#### Review, Verdict, Dispute, And Fraud

- `verdict_engine.py`
  - replaces simplistic truth language with verdict-oriented acceptance logic.
- `evidence_scorer.py`
  - scores support and sufficiency.
- `conflict_classifier.py`
  - classifies disagreement.
- `consensus_validator.py`
  - older consensus-oriented logic that still exists and remains partial relative to the newer verdict framing.
- `result_reviewer.py`
  - local review path for results.
- `review_quorum.py`
  - quorum-oriented review structure.
- `dispute_engine.py`
  - dispute and appeal logic.
- `appeal_queue.py`
  - tracks appeals.
- `fraud_engine.py`
  - suspicious behavior and anti-abuse scoring.
- `challenge_engine.py`
  - challenge-response hooks.
- `proof_of_execution.py`
  - proof receipt scaffolding.
- `hardware_challenge.py`
  - hardware-oriented validation hooks.

Why this layer matters:

- this is the difference between “distributed workers” and a swarm that can at least reason about abuse, disagreement, and challengeability.

#### Knowledge Presence And Swarm Memory

- `knowledge_registry.py`
  - turns local learning shards into manifests and holder records and records remote holders.
- `knowledge_advertiser.py`
  - broadcasts hello, heartbeat, and local knowledge advertisements.
- `knowledge_fetcher.py`
  - requests relevant holders and fetch routes.
- `knowledge_replication.py`
  - replication-oriented helpers for holder growth.
- `knowledge_freshness.py`
  - TTL and freshness utilities.
- `provenance_store.py`
  - stores local provenance metadata.
- `context_manifest.py`
  - captures what evidence was included for helpers or validators.
- `evidence_bundle.py`
  - canonical evidence packaging.

Why this layer matters:

- this is the subsystem that moves NULLA from “peer-aware” to “knowledge-aware.”

#### Meet-And-Greet And Coordination Service

- `meet_and_greet_models.py`
  - typed request and response models for the hot coordination plane.
- `meet_and_greet_service.py`
  - main service facade for presence, knowledge, payment markers, node registry, and snapshots or deltas.
- `meet_and_greet_replication.py`
  - pull-based snapshot and delta replication between meet nodes.

Why this layer matters:

- it is the first shared infrastructure layer meant for friend swarms and early global tests.

#### Economics And Optional Settlement

- `credit_ledger.py`
  - local accounting substrate with replay-minded protections.
- `credit_dex.py`
  - simulated exchange logic.
- `order_book.py`
  - matching and offer logic.
- `reward_engine.py`
  - contribution or reward handling.
- `dna_payment_bridge.py`
  - optional DNA-oriented payment proof export.
- `solana_anchor.py`
  - optional Solana surface.

Why this layer matters:

- it defines where the economic path is headed,
- but it must continue to be described as simulated until the proof and reconciliation story is much stronger.

#### Content, Liquefy, And Bridge Modules

- `liquefy_bridge.py`
  - bridge logic into Liquefy-oriented workflows.
- `liquefy_cas.py`
  - content-addressed and Liquefy-related content bridging.
- `storage_manager.py`
  - storage coordination logic.
- `cold_storage.py`
  - slower or archival storage behavior.

Why this layer matters:

- it is where larger-object storage and archive integration should live, not in the hot metadata plane.

#### Policy And Safety

- `policy_engine.py`
  - current policy load and validation path.
- `execution_gate.py`
  - execution gating.
- `idle_assist_policy.py`
  - controls when a node offers assistance.
- `feedback_engine.py`
  - outcome evaluation and local learning promotion.

#### Other Important Supporting Modules

- `bootstrap_adapters.py`
  - bootstrap-mirror support.
- `bootstrap_sync.py`
  - bootstrap coordination.
- `maintenance.py`
  - periodic upkeep, broadcasts, and housekeeping.
- `memory_manager.py`
  - local memory coordination.
- `final_response_store.py`
  - finalized response persistence.
- `finalizer.py`
  - parent finalization flow.
- `runtime_paths.py`
  - runtime path normalization and portability support.

### `network/`

This directory contains transport, protocol, and wire-level logic.

#### Signed Protocol And Base Transport

- `protocol.py`
  - envelope encoding and protocol behavior.
- `signer.py`
  - local signing and identity-related helpers.
- `transport.py`
  - UDP transport runtime.
- `quarantine.py`
  - peer quarantine behavior.
- `rate_limiter.py`
  - peer and message rate limiting.

#### Assist And Task Messaging

- `assist_models.py`
  - capability ads and assist models.
- `assist_router.py`
  - task offer, claim, assignment, and helper message handling.

#### Knowledge And Presence Messaging

- `knowledge_models.py`
  - knowledge message schemas such as advertisements, refresh, withdrawal, and replication.
- `knowledge_router.py`
  - handling for knowledge message types.
- `presence_router.py`
  - handling for presence-related message types.

#### Larger Payload And Network Readiness

- `chunk_protocol.py`
  - chunking, integrity, and reassembly primitives.
- `stream_transport.py`
  - reliable larger-payload transport layer.
- `transfer_manager.py`
  - transfer orchestration.
- `stun_client.py`
  - STUN-oriented public endpoint discovery scaffold.
- `nat_probe.py`
  - NAT classification.
- `hole_punch.py`
  - hole punching scaffolding.
- `relay_fallback.py`
  - relay selection and fallback behavior.
- `bootstrap_node.py`
  - bootstrap node registry and behavior.
- `dht.py`
  - DHT-related routing support that still remains partial.

Why this layer matters:

- it cleanly separates small UDP control-plane use from the emerging larger-payload transport layer and future WAN-readiness work.

### `storage/`

This directory holds local persistence and metadata indexing.

#### Core Database And Migrations

- `db.py`
  - SQLite connection management.
- `migrations.py`
  - schema management and compatibility updates.

#### Knowledge-Presence Persistence

- `knowledge_index.py`
  - presence leases, index deltas, and related hot metadata.
- `knowledge_manifests.py`
  - manifest records for shards.
- `replica_table.py`
  - holder records and replica metadata.
- `dialogue_memory.py`
  - session memory for human-input adaptation.

#### CAS And Large Object Support

- `cas.py`
  - content-addressed storage layer.
- `chunk_store.py`
  - chunk storage.
- `blob_index.py`
  - blob references.
- `manifest_store.py`
  - reassembly manifest store.

#### Audit And Event Integrity

- `event_log.py`
  - append-only event logging.
- `event_hash_chain.py`
  - chained integrity evidence for important events.

#### Meet-And-Greet Persistence

- `meet_node_registry.py`
  - registry of coordinating nodes and sync state.
- `payment_status.py`
  - hot metadata markers for payment status.

#### Older Or Supporting Persistence

- `swarm_memory.py`
  - previously stored swarm-derived memory or mesh contexts.

### `sandbox/`

This directory is the execution safety boundary.

- `job_runner.py`
  - standardized controlled execution path.
- `resource_limits.py`
  - CPU, time, and memory-style boundaries.
- `network_guard.py`
  - egress restrictions.
- `filesystem_guard.py`
  - workspace and path boundaries.
- `container_adapter.py`
  - pluggable backend adapter path.
- `sandbox_runner.py`
  - sandbox coordination.
- `helper_worker.py`
  - helper-side execution of capsule work.
- `command_simulator.py`
  - command modeling or simulation support.

Why this layer matters:

- it is the beginning of making local and helper execution survive safety scrutiny rather than relying on trust and convention only.

### `ops/`

This directory contains reporting, diagnostics, and test harness utilities.

- `feature_flags_report.py`
  - generates the current implementation-status report.
- `health_report.py`
  - operational health summary.
- `swarm_trace_report.py`
  - trace-level reporting.
- `swarm_knowledge_report.py`
  - knowledge-presence report.
- `replication_audit.py`
  - replication audit logic.
- `benchmark_caps.py`
  - capability benchmarking.
- `integration_smoke_test.py`
  - integration sanity coverage.
- `chaos_test.py`
  - fault-injection-style checks.
- `nulla_user_report.py`
  - user-facing memory and mesh summary rendering.

## 6. Human Input Adaptation Layer

This is one of the highest-priority product improvements added in the recent hardening pass.

### Why It Exists

Without this layer, NULLA would only work well when the user speaks clearly and cleanly.

That is not realistic.

Users will type:

- fragments,
- typos,
- shorthand,
- abrupt references,
- vague “that one” style follow-ups,
- and messy security or system questions.

### What Exists Now

The current input adaptation layer:

- normalizes obvious messy phrasing,
- expands known shorthand,
- stores lightweight session dialogue memory,
- extracts topic hints,
- resolves likely ambiguous references,
- scores understanding confidence,
- stores interpretation artifacts for later summary and learning,
- and feeds that interpretation context into task classification and response shaping.

### Where It Lives

- `core/input_normalizer.py`
- `core/human_input_adapter.py`
- `storage/dialogue_memory.py`
- `apps/nulla_agent.py`
- `core/task_router.py`
- `core/reasoning_engine.py`

### Current Limit

This is still a pragmatic lightweight layer, not a full semantic conversation engine.

It improves usability materially, but it is still early and should be treated as a good foundation rather than a finished language-understanding subsystem.

## 7. Task And Trace Lifecycle

The system now has a more explicit lifecycle than before.

### Current Flow

1. User input is normalized and interpreted.
2. A task record is created with redacted summary and environment hints.
3. A trace identifier is assigned.
4. A state transition is recorded.
5. Classification runs with interpretation context.
6. Parent orchestration decides whether helper involvement is useful.
7. If appropriate, offers are published and claims handled.
8. Results are reviewed and finalized.
9. Durable outcomes can be converted into reusable local knowledge.

### Why This Matters

Without explicit trace and lifecycle state:

- timeout behavior becomes ambiguous,
- failure investigation becomes weak,
- replay and duplicate-work analysis becomes harder,
- and proof reporting becomes vague.

### Current State

The state and trace primitives are implemented, but their live proof story across multiple machines still needs to be completed.

## 8. Knowledge Presence And Swarm Memory

This is one of the biggest architectural advances in the recent work.

### What Changed

The system no longer only knows that peers exist.

It now keeps metadata about:

- live presence,
- shard manifests,
- holder records,
- shard versions,
- freshness,
- replication,
- and fetch routes.

### Core Rule

Advertise metadata first.
Fetch full content on demand.

### Why This Matters

This gives NULLA a real swarm-memory index instead of blind distributed work dispatch.

It enables:

- local-first lookup,
- “who knows what” search,
- reduced duplicate work,
- organic replication,
- and a foundation for meet-and-greet indexing.

### Current Components

#### Local Registration

`core/knowledge_registry.py` converts local durable learning shards into:

- manifests,
- holder records,
- topic tags,
- summary digests,
- and local fetch routes.

#### Broadcast And Presence

`core/knowledge_advertiser.py` and the routers broadcast:

- hello advertisements,
- presence heartbeats,
- knowledge advertisements,
- refreshes,
- and withdrawals.

#### Storage Model

The hot metadata persists in:

- `storage/knowledge_index.py`
- `storage/knowledge_manifests.py`
- `storage/replica_table.py`

#### Fetch Behavior

`core/knowledge_fetcher.py` and related logic request and rank relevant holders.

### Current Proof Gap

The logic exists, but full credibility still depends on live proof for:

- cross-machine advertisement,
- replica acquisition,
- lease expiry,
- version supersession,
- and reconnect or resync convergence.

## 9. Meet-And-Greet Layer

This is the first shared coordination service for friend swarms and early global tests.

### Why It Exists

Direct peer discovery alone is not enough once the swarm grows beyond one local network.

The swarm needs a lightweight way to know:

- who is online,
- which peers are reachable,
- which knowledge manifests are advertised,
- which holders exist,
- how fresh those claims are,
- and which meet nodes exist.

### Core Rule

The meet layer is the hot coordination plane.

It is not:

- the whole memory plane,
- the whole archive plane,
- or the whole payment plane.

### Service Responsibilities

The current service manages:

- presence registration,
- presence heartbeat,
- presence withdrawal,
- active presence listing,
- knowledge advertisement,
- knowledge replication,
- knowledge refresh,
- knowledge withdrawal,
- knowledge search,
- knowledge index listing,
- shard entry detail,
- snapshot export,
- delta listing,
- meet-node registration,
- meet-node listing,
- meet sync state,
- and payment status markers.

### Current API Surface

The detailed contract lives in `docs/MEET_AND_GREET_API_CONTRACT.md`.

At a high level, the service exposes:

- presence endpoints,
- knowledge endpoints,
- snapshot and delta endpoints,
- cluster node endpoints,
- payment status endpoints,
- and health.

### Replication Model

The current replication path is pull-based:

- meet nodes store deltas,
- peers pull deltas by cursor,
- if necessary they fall back to snapshot,
- and sync cursors are tracked per remote node.

This is intentionally simpler and more practical for early Windows, Linux, and macOS friend swarms than a more aggressive push-first or consensus-heavy design.

### Current Proof Gap

The service and replication path are implemented, but:

- live redundant deployment,
- global convergence,
- failover behavior,
- and region-aware federation

still need more work.

## 10. Content Plane, CAS, And Liquefy

The system now has the right direction for larger-object handling.

### Core Rule

Hot metadata stays plain.
Payloads get chunked and stored separately.
Archives and larger bundles can be packed.

### Current Components

- `storage/cas.py`
- `storage/chunk_store.py`
- `storage/blob_index.py`
- `storage/manifest_store.py`
- `network/chunk_protocol.py`
- `network/stream_transport.py`
- `network/transfer_manager.py`
- `core/liquefy_bridge.py`
- `core/liquefy_cas.py`

### Intended Use

This plane should own:

- shard bodies,
- larger manifests,
- result bundles,
- archive exports,
- proof batches,
- and other larger replicated content.

### Liquefy Position

Liquefy is the right partner for:

- compression,
- bundling,
- deduplicated storage,
- searchable archives,
- and sync or export artifacts.

Liquefy is not supposed to become the hot live index for every heartbeat and lease row.

## 11. Payment And Proof Plane

This area exists, but it is intentionally not being overstated.

### Current Components

- `core/credit_ledger.py`
- `core/credit_dex.py`
- `core/dna_payment_bridge.py`
- `core/order_book.py`
- `storage/payment_status.py`

### Current Truth

The accounting and payment path is still simulated.

The codebase now says that more clearly than before.

### Intended Shape

The intended long-term shape is:

- local accounting and replay protection,
- async payment sidecar events,
- proof artifacts,
- and optional settlement integration.

### What Must Not Happen

The payment rail must not be allowed to block hot coordination.

This is why the meet layer exposes only payment status markers, not full proof logic inline.

## 12. Safety And Execution Boundary

Safety has improved, but it should still be spoken about with engineering precision.

### Helper Capsule Model

Helpers are not supposed to receive raw unrestricted user state.

The system constrains helper work toward:

- reasoning,
- validation,
- ranking,
- comparison,
- and summarization.

### Local Execution Boundary

The sandbox layer now provides:

- explicit job-running path,
- time, memory, and similar resource boundaries,
- workspace-aware filesystem restrictions,
- restricted network egress,
- and a backend adapter path for stronger future isolation.

### Why This Matters

This is the practical start of making NULLA safe enough to grow without pretending a shell wrapper is a full compute security model.

## 13. User-Facing Memory And Mesh Summary

This was added because users need an understandable view of what their local NULLA instance knows and does.

### What It Shows

The summary now covers:

- local identity and persona,
- local learning counts,
- mesh-derived learning counts,
- indexed shard counts,
- active peer counts,
- recent finalized responses,
- recent mesh-derived context,
- recent interpreted dialogue,
- and conservative outbound or inbound activity summaries.

### Where It Lives

- `core/nulla_user_summary.py`
- `ops/nulla_user_report.py`
- `apps/nulla_cli.py`

### Why It Matters

A user should not have to inspect internal tables to understand:

- what NULLA learned locally,
- what came from the mesh,
- what is indexed,
- and what activity has happened recently.

## 14. Data Model And Persistence

The database is still local-first SQLite.

That is acceptable for the current phase because NULLA remains local-first.

### Important Table Families

The schema is spread across migrations and helper modules, but the important storage concepts are:

#### Task State

- local tasks,
- task results,
- task reviews,
- finalized responses.

Purpose:

- task lifecycle, review, outcome, and user-facing response history.

#### Learning And Knowledge

- learning shards,
- manifests,
- holder records,
- knowledge presence rows,
- replication metadata.

Purpose:

- store what this node knows and what the swarm claims to know.

#### Dialogue Memory

- session-level dialogue memory,
- shorthand lexicon,
- recent interpreted turns.

Purpose:

- better human-input adaptation over time.

#### Audit And Event Integrity

- audit logs,
- event log,
- event hash chain.

Purpose:

- traceability,
- tamper evidence,
- and proof collection.

#### Meet Infrastructure

- meet-node registry,
- sync state,
- payment markers,
- index deltas.

Purpose:

- shared hot coordination plane and recovery.

### Important Caveat

SQLite is still a local node truth store, not a global trustless authority.

That is fine for now, but the system should continue to describe global truth carefully.

## 15. Proof State And Validation Truth

The codebase is now past the stage where vision docs are the bottleneck.

The bottleneck is runtime proof.

### What Is Already Proved Locally

As of the latest local pass, automated local coverage exists for:

- full suite baseline: `188 passed, 5 skipped, 1 warning`,

- conflicting helper output handling,
- sandbox boundary enforcement,
- fraud trigger behavior,
- chunk-level integrity logic,
- ledger replay-related local protections,
- meet service behavior,
- and knowledge-presence component behavior.

### What Still Needs Live Evidence

- full cross-machine LAN task lifecycle,
- helper death recovery,
- event-chain tamper detection,
- full replay rejection at the wire level,
- live transfer corruption or retry behavior,
- cross-machine knowledge advertisement,
- replica acquisition and re-advertisement,
- lease expiry and offline prune,
- version supersession,
- reconnect or resync convergence,
- and meet-node convergence across real machines.

### Current Documentation Gate

The proof status is currently held in:

- `docs/PROOF_PASS_REPORT.md`
- `docs/LAN_PROOF_CHECKLIST.md`

These files should remain the gate for any stronger runtime claims.

## 16. Scaling Direction

The right scaling direction is now clear enough to document explicitly.

### Wrong Shape

Do not build:

- one giant global meet brain,
- every machine as a meet node,
- or full-content replication in the hot meet layer.

### Right Shape

Scale as:

- personal local-first NULLA agents,
- small regional meet clusters,
- pull-based replication,
- regional detailed metadata,
- global summarized routing hints,
- P2P content fetch,
- and async payment or proof sidecars.

### First Global Test Shape

For the first mixed-platform global test:

- use three meet nodes,
- keep ordinary machines as agents with local cache,
- and prove convergence before expanding the coordinator count.

### Next Scaling Step

The next actual deployment step should be:

- use the active `do_ip_first_4node` pack for first closed internet-connected testing,
- collect proof artifacts from the current EU/US/APAC + watcher topology,
- then promote to `global_3node` DNS/TLS config only after convergence and failover proof is recorded,
- prove regional-detail and global-summary behavior across live hosts,
- and avoid flooding every lease and micro-update globally.

## 17. Integration Surfaces

This is where the system is meant to plug into adjacent stacks without collapsing its own local-first identity.

### Liquefy / OpenClaw

Role:

- content and archive plane partner.

Use for:

- packed shard bodies,
- archive bundles,
- searchable snapshot exports,
- proof or receipt bundles,
- and CAS-oriented sync artifacts.

Do not use as:

- the only live metadata store,
- or the thing every heartbeat depends on.

OpenClaw-style channels should be used as:

- user access surfaces,
- message ingress and egress adapters,
- and companion transport layers for Telegram, Discord, and similar platforms.

They should not be used as:

- the place where NULLA memory lives,
- the place where canonical truth is decided,
- or a bypass around NULLA input adaptation, routing, and policy layers.

### DNA Payments

Role:

- async proof and payment sidecar.

Use for:

- payment event export,
- signed receipt artifacts,
- settlement evidence,
- and later premium-service accounting trails.

Do not use as:

- the live coordination dependency for routing and discovery.

### Solana

Role:

- optional settlement or anchoring surface.

Current state:

- present as an integration surface, not a required runtime dependency.

### Standalone Device Product

Role:

- still the primary product identity.

Current rule:

- nothing in the integration stack should break the fact that NULLA remains useful on one device without sidecars.

## 18. Meet-And-Greet Build Status

The meet layer is currently between architecture and deployment.

### Already Done

- service models,
- service logic,
- HTTP dispatch scaffold,
- node registry,
- sync state,
- snapshot export,
- delta listing,
- pull replication,
- health surface,
- and payment status markers.

### Not Yet Done Enough

- region-aware federation,
- home-region model,
- live redundant deployment proof,
- failover proof,
- cross-region convergence proof,
- broader operational packaging.

## 19. Release Readiness

This is a distinct concern from runtime architecture.

### What Exists

- migrations,
- local boot flow,
- feature-status reporting,
- multiple runtime entry points,
- and a credible system map.

### What Is Still Missing

- unified release metadata and version contract,
- compatibility policy across mixed node versions,
- update manifest and upgrade path,
- packaging for mixed desktop platforms,
- role-based deployment profiles for agent versus meet node,
- and smoother friend-to-friend install flow.

### Honest Conclusion

The system is architecturally on the right track for larger scale.

It is not yet release-engineered for mass GitHub distribution and automatic update management.

## 20. License-Safe Model Integration Layer

The codebase now includes a license-safe model/provider boundary.

Purpose:

- keep NULLA core model-agnostic,
- support external teacher/helper engines,
- avoid bundling third-party weights into core,
- and preserve the existing core license posture.

Main files:

- `core/model_registry.py`
- `core/model_capabilities.py`
- `core/model_selection_policy.py`
- `core/model_teacher_pipeline.py`
- `storage/model_provider_manifest.py`
- `adapters/base_adapter.py`
- `adapters/openai_compatible_adapter.py`
- `adapters/local_subprocess_adapter.py`
- `adapters/local_model_path_adapter.py`
- `adapters/optional_transformers_adapter.py`
- `ops/license_audit.py`
- `docs/MODEL_INTEGRATION_POLICY.md`
- `docs/THIRD_PARTY_LICENSES.md`
- `third_party/NOTICES/`

Important truth:

- provider/model entries are manifest-driven
- license metadata is required for safe enablement
- startup warns if metadata is missing
- provider outputs are candidate knowledge only
- nothing in this layer should auto-promote model output into canonical swarm truth

## 21. Immediate Next Work

The cleanest next order remains:

1. complete the remaining live proof runs,
2. use `do_ip_first_4node` as the first closed internet-connected deployment pack,
3. run the live cross-region sync and failover proof pass on that topology,
4. update `global_3node` only as the follow-on DNS/TLS profile,
5. include phones in the controlled team test as companion and mirror devices,
6. verify that Telegram and Discord style channel flows still pass through normal NULLA task and memory policy,
7. run the mobile and channel proof pack across web companion, Telegram, and Discord surfaces,
8. register real provider manifests instead of only the sample file,
9. prepare friend-to-friend distribution packaging,
10. then build the meet-and-greet onboarding experience,
11. then continue with broader release engineering.

## 22. Non-Goals And Guardrails

The following points should remain explicit guardrails:

- Do not market or describe this as trustless public compute yet.
- Do not describe credits as production settlement yet.
- Do not describe WAN routing as solved yet.
- Do not collapse the meet layer, content layer, and payment layer into one system.
- Do not remove local-first fallback.
- Do not force Liquefy or payment sidecars as mandatory runtime dependencies.
- Do not let every agent machine become infrastructure.
- Do not bundle third-party model weights into the repo.
- Do not embed GPL or AGPL runtime code into NULLA core.

## 23. Next-Agent Explicit Checklist

The next agent should treat the following as already-known open tasks:

1. Replace placeholder base URLs and sample paths in:
   - `config/meet_clusters/do_ip_first_4node/*.json`
   - `config/meet_clusters/global_3node/*.json` (only for DNS/TLS follow-on)
   - `config/model_providers.sample.json`
2. Choose real EU / US / APAC meet hosts and freeze the first regional mapping.
3. Run live proof for:
   - cross-region snapshot sync
   - same-region delta sync
   - summarized remote holder views
   - failover and reconnect
   - abuse-gossip propagation convergence
4. Include phone-companion proof before wider sharing:
   - phone receives bounded summaries and hot metadata only
   - phone reconnect does not corrupt presence or session state
   - phone is not treated as a default meet node
5. Include channel-gateway proof before wider sharing:
   - Telegram and Discord style channel input still passes through NULLA normalization
   - tiered context and memory-first routing still apply
   - channel-derived output stays under candidate-versus-canonical knowledge rules
6. Run the mobile and channel checklist:
   - web companion path
   - Telegram path
   - Discord path
   - phone reconnect and bounded-cache behavior
7. Register real provider manifests with verified license metadata before enabling them.
8. Replace placeholder license files with approved final texts before any public release.
9. Keep weights external or user-supplied.
10. Keep payment rails simulated until release and settlement work is actually done.

## 24. Bottom-Line Internal Assessment

This codebase is now materially stronger than an idea-stage distributed agent project.

It has:

- a real local product path,
- a real distributed runtime path,
- a real swarm-memory metadata layer,
- a real meet-and-greet service scaffold,
- a real safety and audit direction,
- and a real path to regional federation without rebuilding from zero.

Its current weak points are no longer mainly conceptual.

They are:

- runtime proof,
- deployment proof,
- region-aware federation,
- and release engineering.

That is a healthier stage than where the project started.

The correct internal posture is:

- protect the local-first product,
- prove the runtime honestly,
- keep economic claims disciplined,
- and scale the meet layer conservatively.
