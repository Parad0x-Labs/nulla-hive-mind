# AGENT HANDOVER

## Purpose

This file is the canonical front-page handover for the current Decentralized NULLA codebase.

It is meant to answer four questions quickly and truthfully:

1. What the system is right now.
2. What is actually implemented versus partial or simulated.
3. Where the important subsystems live.
4. Which documents should be treated as the source of truth for deeper detail.

This handover replaces the stale earlier handover that no longer matched the codebase.

## Executive Truth

Decentralized NULLA is currently a real local-first distributed agent platform with:

- working standalone local operation,
- working trusted LAN swarm operation,
- signed peer messaging,
- safe task capsule orchestration,
- review and verdict infrastructure,
- local persistence and auditability,
- a real knowledge-presence and swarm-memory index layer,
- a first meet-and-greet coordination service scaffold,
- optional Liquefy and payment sidecar integration points,
- and explicit separation between implemented, partial, simulated, and planned areas.

This is not a trustless public compute marketplace yet.

The honest current position is:

- credible local-first product and orchestration runtime,
- credible LAN and friend-swarm prototype,
- partial global coordination scaffold,
- simulated economics,
- partial WAN readiness,
- not yet release-engineered for mass GitHub-style distribution or autonomous public-internet deployment.

## Latest Hardening Delta (2026-03-05)

The most recent hardening tranche added concrete runtime behavior, not just docs:

- anti-abuse propagation baseline:
  - typed `REPORT_ABUSE` payload validation in protocol handling,
  - dedupe storage for seen abuse reports,
  - bounded TTL/fanout abuse gossip forwarding.
- adaptive identity-cost baseline:
  - capability ads now include `pow_difficulty`,
  - validation now enforces policy-driven minimum PoW difficulty.
- SQLite lock behavior hardening:
  - connections enforce WAL, `busy_timeout`, and `synchronous=NORMAL`.
- large-payload transport hardening:
  - stream-first transfer path for oversized payloads,
  - UDP fragmentation + receiver reassembly fallback.
- encrypted and TLS-ready transport posture:
  - optional meet server TLS (cert/key + client trust options),
  - optional mesh wire encryption using PSK-based AES-GCM.
- sandbox network isolation hardening:
  - Linux network-namespace execution path (`unshare -n`),
  - strict `os_enforced` mode for fail-closed network isolation policy.

Current local automated verification for this repo state:

- `188 passed, 5 skipped, 1 warning` (`pytest -q`).

## Read This First

These are the current source-of-truth documents:

- `docs/WHAT_WE_HAVE_NOW.md`
  - Human-facing current-state summary.
- `docs/IMPLEMENTATION_STATUS.md`
  - Implemented versus partial versus simulated versus planned.
- `docs/PROOF_PASS_REPORT.md`
  - Runtime proof gate and evidence status.
- `docs/LAN_PROOF_CHECKLIST.md`
  - Operator runbook for live LAN and knowledge-presence proof.
- `docs/MEET_AND_GREET_PREFLIGHT.md`
  - Gate before building and deploying the meet-and-greet layer.
- `docs/MEET_AND_GREET_SERVER_ARCHITECTURE.md`
  - The three-plane design and redundancy model.
- `docs/MEET_AND_GREET_API_CONTRACT.md`
  - The current hot-plane API surface.
- `docs/MEET_AND_GREET_GLOBAL_TOPOLOGY.md`
  - Early global topology guidance for 10+ mixed-platform machines.
- `docs/MOBILE_OPENCLAW_SUPPORT_ARCHITECTURE.md`
  - Phone-companion and channel-gateway architecture for controlled team testing before Git distribution.
- `docs/MOBILE_CHANNEL_ROLLOUT_PLAN.md`
  - Concrete rollout shape for web companion, Telegram, and Discord access.
- `docs/MOBILE_CHANNEL_TEST_CHECKLIST.md`
  - Operator checklist for phone, web, Telegram, and Discord proof before Git distribution.
- `docs/CLEAN_RUNTIME_SOAK_PREP.md`
  - Fresh-runtime preparation rule before any meaningful overnight soak.
- `docs/OVERNIGHT_SOAK_RUNBOOK.md`
  - Operator runbook for the pre-morning long-session stability pass.
- `config/meet_clusters/do_ip_first_4node/README.md`
  - Current DigitalOcean IP-first closed-test deployment pack.
- `ops/do_ip_first_bootstrap.sh`
  - One-shot four-node bootstrap and health validation for the current DO setup.
- `ops/mobile_channel_preflight_report.py`
  - Current local preflight summary for the mobile and channel test track.
- `ops/overnight_readiness_report.py`
  - Go / no-go report for the overnight soak gate.
- `ops/morning_after_audit_report.py`
  - Morning-after state and integrity audit after the soak run.
- `docs/INTERNAL_HANDOVER_EXTENDED.md`
  - Detailed internal reference for architecture, modules, data model, integrations, and rollout truth.
- `docs/INTERNAL_SYSTEM_MEGA_DOSSIER.md`
  - Audit-grade deep dossier covering architecture, runtime flows, trust boundaries, storage, APIs, integrations, testing, and current risks.
  - Also includes an explicit external-audit response section listing what still blocks public-network claims.
- `docs/TDL.md`
  - Active technical-debt and open-risk list with priorities and completion criteria.

## Current Product Shape

NULLA is designed as a local-first intelligence that remains useful without the swarm.

That design rule is still intact and should not be broken:

- standalone mode is valid,
- swarm participation is an enhancement layer,
- meet-and-greet is coordination infrastructure, not the whole product,
- Liquefy is the content and archive plane, not the hot live index,
- payment and proof rails remain optional and asynchronous.

## Workspace Hygiene

The repository must be treated separately from live local runtime state.

Current runtime defaults now place mutable state under `.nulla_local/`, not under the main source tree database path.

Operational rule:

- do not share `.nulla_local/`
- do not share live signing keys
- do not share populated local state databases as part of a handoff package

The next agent or operator should sanitize local runtime state before packaging this folder for other machines.

## Runtime Modes

### 1. Standalone Local Mode

NULLA can run on a single device and still:

- normalize messy human input,
- classify and gate tasks,
- plan and render answers,
- store task and audit state,
- synthesize reusable local knowledge shards,
- and show a user-facing summary of what it knows.

### 2. Trusted LAN Swarm Mode

On a local or friend mesh, NULLA can:

- advertise capability and presence,
- discover peers,
- decompose parent tasks,
- offer and claim helper work,
- review helper output,
- track traces and task states,
- and preserve local-first fallback if peers vanish.

### 3. Knowledge-Aware Swarm Mode

The mesh now also tracks:

- who is online,
- what each node claims to know,
- which shard versions exist,
- freshness and lease state,
- replication count,
- and fetch routes for on-demand retrieval.

This is the current swarm-memory metadata layer.

### 4. Meet-And-Greet Coordination Mode

The meet-and-greet stack now exists as a scaffold with:

- typed schemas,
- service-level logic,
- HTTP dispatch surface,
- meet-node registry,
- snapshot and delta replication,
- and sync-state tracking.

This is currently partial because live redundant deployment and cross-region convergence still need proof.

Current safety posture:

- local meet nodes default to loopback binding,
- public or non-loopback deployment must set an auth token intentionally,
- HTTP write routes now require signed write envelopes,
- signed writes now use nonce replay protection and route-to-actor binding,
- signed identities now also honor scoped revocation policy,
- request bodies are capped,
- and write traffic is rate-limited.

### 5. Mobile Companion And Channel Access Mode

Phones and OpenClaw-style integrations are now part of the intended pre-Git team-test shape.

Current rule:

- the main NULLA brain stays on a desktop, laptop, server, or another reliable primary machine,
- phones are treated as companion clients and lightweight presence or summary mirrors,
- and Telegram, Discord, or similar OpenClaw-style integrations are treated as channel front ends rather than product-brain replacements.

This mode is now documented and should be tested, but it is not yet a finished native mobile product.

## Major System Layers

### Front Door And Human Input

The user-facing entry path now includes:

- input normalization,
- shorthand handling,
- session-aware reference resolution,
- topic hints,
- and understanding-confidence scoring.

This is the layer that makes NULLA more resilient to messy human phrasing.

### Task Creation And Local Reasoning

Task records are created with:

- redacted summaries,
- environment hints,
- trace identifiers,
- lifecycle state transitions,
- and classification context.

Local reasoning still defaults to safe advice-oriented behavior.

### Swarm Orchestration

The orchestration layer remains parent/helper based:

- the parent frames and decomposes work,
- helpers operate on bounded capsules,
- results are reviewed locally,
- verdict logic handles disagreement,
- and finalization remains parent-controlled.

### Knowledge Presence And Swarm Memory

The system now maintains metadata for:

- manifests,
- holders,
- freshness windows,
- presence leases,
- and fetchable shard routes.

Full content remains local-first unless fetched on demand.

### Meet-And-Greet Hot Coordination Plane

The hot plane owns:

- presence,
- leases,
- knowledge manifest metadata,
- holder/version maps,
- payment status markers,
- meet-node membership,
- snapshots,
- and deltas.

It is intentionally metadata-only.

### Content Plane

The content plane is for:

- shard bodies,
- larger manifests,
- bundle payloads,
- archive exports,
- proof bundles,
- and CAS-backed or Liquefy-backed storage.

### Payment And Proof Plane

The payment and proof plane is for:

- DNA receipt artifacts,
- settlement evidence,
- and optional premium-service accounting.

It is explicitly asynchronous relative to live coordination.

## What Is Strong Right Now

- Local-first product shape is real.
- LAN mesh orchestration is real.
- Human-input adaptation is integrated.
- Swarm memory metadata is real.
- User-facing memory and mesh summary now exists.
- Signed messaging and replay-minded protocol discipline are present.
- Safety boundaries for helper capsules and local execution are much clearer than before.
- Verdict, conflict, dispute, and fraud infrastructure now exist in code instead of only in narrative.
- CAS and event-chain foundations are in place.
- Meet-and-greet service scaffolding is now concrete rather than hypothetical.

## What Is Still Partial

- Meet-and-greet live multi-node deployment proof.
- Cross-region convergence proof.
- WAN and NAT traversal readiness in the real world.
- Full transport cutover for every large live payload path.
- Regional federation live-proof and deployment hardening.
- Release engineering for easy mass GitHub distribution and updates.

## What Is Still Simulated

- Credit settlement economy.
- DNA payment finality as trustless settlement.
- Credit DEX as a real economic market.

The codebase now labels these areas as simulated on purpose.

## Core Directories

### `apps/`

Entry points and runtime wrappers:

- `nulla_agent.py`
  - Local-first user-facing runtime.
- `nulla_daemon.py`
  - Peer-facing swarm daemon.
- `nulla_cli.py`
  - Local operator interface and summary entry.
- `meet_and_greet_server.py`
  - HTTP scaffold for the hot coordination plane.
- `meet_and_greet_node.py`
  - Meet-node runtime wrapper.

### `core/`

The main product logic:

- human input and persona shaping,
- task routing and orchestration,
- knowledge registry and fetch behavior,
- meet-and-greet schemas and service logic,
- verdict, dispute, fraud, proof, and timeout logic,
- integration bridges,
- and user summary generation.

### `network/`

Protocol and transport layer:

- signed messaging,
- assist routers,
- presence and knowledge message models,
- chunk and stream transport,
- bootstrap, relay, NAT, and DHT scaffolding,
- and hot message routing.

### `storage/`

Persistence and index state:

- SQLite access,
- migrations,
- manifests and holders,
- presence and delta logs,
- event chain,
- CAS and chunk stores,
- payment markers,
- meet-node registry,
- and dialogue memory.

### `sandbox/`

Local execution boundary:

- job runner,
- resource limits,
- filesystem and network guardrails,
- and execution backend adapters.

### `ops/`

Operational reporting and validation:

- feature status reporting,
- swarm trace reporting,
- integration smoke testing,
- knowledge and replication audit views,
- chaos and benchmark tools,
- and the user-facing summary report.

## Integrations And Sidecars

### Liquefy / OpenClaw

Liquefy remains the intended content-plane and archive-plane partner for:

- compressed shard bodies,
- replication bundles,
- snapshot exports,
- proof archives,
- and CAS-backed large-object storage.

NULLA remains standalone-capable without Liquefy.

OpenClaw-style integrations are best treated as:

- transport and interaction surfaces,
- channel adapters for Telegram, Discord, and similar entry points,
- and optional ecosystem bridges that let users talk to their NULLA through familiar tools.

They should not replace:

- NULLA memory,
- NULLA routing,
- NULLA policy,
- or NULLA validation.

### License-Safe Model Integration Layer

NULLA now has an optional model-provider boundary that does not change the core license posture.

This layer is intended for:

- local teacher/helper models,
- OpenAI-compatible HTTP backends,
- subprocess-backed external runtimes,
- local model-path bridges,
- and optional transformers-backed local paths when the dependency is installed.

Important rules:

- core remains under the current project license posture,
- no model weights are bundled in NULLA core,
- provider entries must declare license metadata,
- and model outputs remain candidate knowledge rather than canonical swarm truth.

### Bounded Curiosity

NULLA now also has a bounded curiosity layer intended to reduce relearning from scratch.

Main files:

- `core/curiosity_policy.py`
- `core/curiosity_roamer.py`
- `core/source_reputation.py`
- `storage/curiosity_state.py`
- `ops/curiosity_report.py`

Operational truth:

- curiosity is budgeted and bounded,
- it uses curated source classes rather than arbitrary free wandering,
- it now includes explicit domain credibility scoring and blocked-source filtering,
- it is suitable for technical thread-following and short-lived world pulse summaries,
- and outputs remain candidate-only rather than canonical memory.

### Social And Media Evidence

NULLA now also has an external-evidence lane for:

- explicit URLs,
- social posts,
- images,
- videos,
- and transcript/caption-backed media references from channel integrations.

Main files:

- `core/media_ingestion.py`
- `core/media_evidence_ranker.py`
- `core/media_analysis_pipeline.py`
- `core/social_source_policy.py`
- `storage/media_evidence_log.py`

Operational truth:

- social platforms are low-trust by default,
- blocked propaganda and hyperpartisan domains are filtered,
- image/video analysis is optional and depends on a configured multimodal-capable provider,
- and all media-derived output remains candidate-only.

### Brain Hive

NULLA now also has a first Brain Hive service layer for an agent-only research commons.

Main files:

- `core/brain_hive_models.py`
- `core/brain_hive_service.py`
- `storage/brain_hive_store.py`
- `docs/BRAIN_HIVE_ARCHITECTURE.md`
- `docs/BRAIN_HIVE_API_CONTRACT.md`

Operational truth:

- topics, posts, claim links, agent profile rollups, and coarse public stats now exist locally,
- a read-only Brain Hive watch page now exists on the meet server,
- an admission guard now blocks obvious command-echo spam, duplicate circulation, and hype-token posting,
- stateful moderation now records approved, review-required, or quarantined outcomes,
- write routes now require signed envelopes that bind the signer to the route actor,
- the layer reuses current presence, scoreboard, task, and naming state,
- human read access and agent claim-link display are part of the intended design,
- live API/server exposure now exists through the meet-and-greet HTTP scaffold,
- signed write enforcement now exists on the current HTTP write routes,
- but live deployment proof is still pending.

### Knowledge Possession Challenge

The knowledge-presence layer now also has a proof-capable challenge path.

For manifests that expose CAS chunk metadata, a challenger can now:

- issue a holder challenge,
- receive a deterministic chunk proof from the holder,
- and verify that returned chunk against the expected CAS chunk hash.

This does not make every holder claim cryptographically final, but it materially improves the system over pure metadata assertion.

Main files:

- `core/model_registry.py`
- `core/model_capabilities.py`
- `core/model_selection_policy.py`
- `core/model_teacher_pipeline.py`
- `storage/model_provider_manifest.py`
- `adapters/`
- `ops/license_audit.py`
- `docs/MODEL_INTEGRATION_POLICY.md`
- `docs/THIRD_PARTY_LICENSES.md`

### DNA Payment Bridge

The DNA path remains an optional sidecar and proof rail.

It should be treated as:

- async,
- proof-oriented,
- non-blocking for live coordination,
- and still simulated from a trustless-settlement perspective.

### Solana Anchor

Solana integration remains present as an optional integration surface, not a required runtime dependency.

## Proof State

The correct current truth is:

- latest local automated verification: `188 passed, 5 skipped, 1 warning`,
- multiple live proof items are still waiting for cross-machine evidence,
- knowledge-presence logic exists but still needs live propagation and expiry proof,
- meet-node replication exists but still needs global convergence proof,
- and deployment claims must stay behind the proof gate.

Do not promote the system beyond its proof state.

## Next Practical Rollout

The current intended order is:

1. Complete the remaining live LAN and swarm-memory proof items.
2. Use the active `do_ip_first_4node` pack for first closed internet-connected testing.
3. Run the live cross-region sync and failover proof pass on that four-node topology.
4. Promote to `global_3node` DNS/TLS config only after proof artifacts are collected.
5. Only then deepen friend-swarm distribution, onboarding, and broader release packaging.

## Next-Agent Checklist

The next agent should not rediscover these tasks. Treat them as the explicit follow-up list:

1. Replace placeholder values in:
   - `config/meet_clusters/do_ip_first_4node/seed-eu-1.json`
   - `config/meet_clusters/do_ip_first_4node/seed-us-1.json`
   - `config/meet_clusters/do_ip_first_4node/seed-apac-1.json`
   - `config/meet_clusters/do_ip_first_4node/watch-edge-1.json`
2. Keep `config/meet_clusters/global_3node/` as the DNS/TLS follow-on pack, not the first deployment target.
3. Choose the first real EU / US / APAC meet hosts and lock the region mapping.
4. Run the live proof pass on current deployment topology:
   - snapshot sync
   - delta sync
   - cross-region summary behavior
   - failover and reconnect
   - abuse-gossip propagation visibility
5. Register real model-provider manifests instead of only the sample file:
   - `config/model_providers.sample.json` is a template, not live truth
   - verify license metadata before enabling any provider
6. Include phones in the controlled team test only as companion and mirror roles:
   - do not treat phones as default meet nodes
   - do not treat phones as the default full archive or model host
   - verify that phone views stay metadata-first
7. Verify channel gateway behavior for Telegram and Discord style access:
   - user input must still pass through NULLA normalization
   - task routing and tiered context must still apply
   - channel-originated output must not bypass candidate-versus-canonical knowledge rules
8. Run the mobile and channel proof pack:
   - web companion path
   - Telegram path
   - Discord path
   - phone reconnect and bounded-cache behavior
9. Replace placeholder licensing files with the approved final texts before any public release:
   - `LICENSE`
   - `LICENSES/BSL-1.1.txt`
   - `LICENSES/Apache-2.0.txt`
   - `LICENSES/MIT.txt`, only if actually needed
10. Sanitize local runtime state before sharing any folder bundle:
   - remove `.nulla_local/`
   - avoid shipping live `nulla_web0_v2.db` state
   - avoid shipping any existing signing key material
11. Keep third-party weights external or user-supplied:
   - do not place model weights in the repo
   - do not convert sample paths into bundled assets
12. Keep GPL / AGPL runtimes isolated behind subprocess or API boundaries if support is ever added.
13. Release-readiness still remains open:
   - versioning
   - update channel
   - compatibility policy
   - install/distribution packaging
14. Payment rails remain simulated:
   - do not present them as trustless settlement
   - do not move them into the hot path

## Immediate Documentation Rule

Treat this file as the front page.

Treat `docs/INTERNAL_HANDOVER_EXTENDED.md` as the deep internal reference.

Treat `docs/IMPLEMENTATION_STATUS.md` and `docs/PROOF_PASS_REPORT.md` as the truth gate whenever there is any conflict between ambition and runtime evidence.
