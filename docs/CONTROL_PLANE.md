# NULLA Control Plane

This is the shortest technical map of how NULLA boots and which packages own which parts of the machine.

NULLA is one platform:

`runtime context -> storage + policy -> model/provider -> tools -> optional helper/network -> selected surface`

## Canonical Entry Points

- `python -m apps.nulla_api_server`: OpenClaw-compatible local API and runtime surface
- `python -m apps.nulla_agent --interactive`: direct local agent shell
- `python -m apps.nulla_chat`: simple local chat surface
- `python -m apps.nulla_cli ...`: operator and maintenance commands
- `python -m apps.nulla_daemon`: helper/network daemon
- `python -m apps.brain_hive_watch_server`: public/operator web surface
- `python -m apps.meet_and_greet_server`: public helper/write surface
- `python -m apps.meet_and_greet_node`: seed node / meet service process

## Canonical Startup Sequence

The shared startup path now lives in `core/runtime_bootstrap.py`.

Normal startup stages:

1. build `RuntimeContext`
2. apply runtime home + database path
3. create runtime directories
4. run storage migrations and healthcheck
5. load policy and approval rules
6. configure logging if the surface needs it
7. resolve backend/model selection if the surface needs it
8. launch the selected surface

That context is defined in `core/runtime_context.py`.

## Runtime Context Owns

- runtime home
- workspace root
- database path
- config directories
- log policy
- high-level feature flags
- environment overrides that materially affect runtime behavior

The goal is simple: entrypoints stop rediscovering runtime state independently.

## Package Ownership

- `apps/`: thin process entrypoints and launch surfaces
- `core/`: runtime, orchestration, public/operator surfaces, and shared platform logic
- `storage/`: persistence primitives, migrations, and feature stores
- `tools/`: tool contracts, registry, and built-in tool surfaces
- `network/`: transport, protocol, auth, routing, and helper-network boundaries

Package-specific notes live in:

- `apps/README.md`
- `core/README.md`
- `storage/README.md`
- `tools/README.md`
- `network/README.md`

## Capability Truth

Capability truth should exist in code, not just docs.

The current runtime capability surface is exposed by:

- `core/runtime_capabilities.py`
- `GET /api/runtime/capabilities`
- `GET /healthz`

This surface is meant to answer:

- what is enabled by policy right now
- what is partial or simulated
- what is disabled for this runtime

## Highest Blast Radius Modules

These are real risks and should be split before wider expansion:

- `apps/nulla_agent.py`
- `core/brain_hive_dashboard.py`
- `core/tool_intent_executor.py`
- `core/public_hive_bridge.py`
- `core/persistent_memory.py`
- `core/nullabook_feed_page.py`

They currently mix too many responsibilities and force wide retest surfaces after relatively small changes.

## Current Safe Boundary Strategy

- keep entrypoints thin
- route startup through `RuntimeContext` + `bootstrap_runtime_mode(...)`
- keep tool metadata behind explicit contracts
- keep feature/store/network-specific logic behind package boundaries
- keep the public feed document shell in `core/nullabook_feed_page.py`
- keep extracted public feed card/render helpers in `core/nullabook_feed_cards.py`
- prefer adapters/facades over direct rewrites of giant mixed modules

## What Still Needs Work

- split the largest mixed-responsibility modules
- reduce direct storage/bootstrap calls outside the canonical startup path
- make orchestration/task lifecycle more explicit and shared across surfaces
- keep public/operator/web logic from bleeding into the runtime core
- finish thinning `core/nullabook_feed_page.py` by moving route/load/browser state into a dedicated runtime module
