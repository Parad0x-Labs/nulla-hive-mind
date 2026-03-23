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
The shared startup-and-provider audit surface for operator/chat entrypoints now lives in `core/runtime_backbone.py`.

Normal startup stages:

1. build `RuntimeContext`
2. apply runtime home + database path
3. create runtime directories
4. run storage migrations and healthcheck
5. load policy and approval rules
6. configure logging if the surface needs it
7. resolve backend/model selection if the surface needs it
8. surface hardware tier + provider audit truth through the runtime backbone when the surface needs it
9. route role-aware provider selection through `core/provider_routing.py` when the surface needs explicit drone/queen behavior
10. launch the selected surface

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
- `core/dashboard/workstation_client.py`
- `core/dashboard/workstation_render.py`
- `core/agent_runtime/hive_topic_create.py`
- `core/nullabook_feed_page.py`
- `core/brain_hive_service.py`
- `core/runtime_task_rail.py`
- `core/public_hive_bridge.py`
- `core/agent_runtime/hive_research_followup.py`

They currently mix too many responsibilities and force wide retest surfaces after relatively small changes.

## Current Safe Boundary Strategy

- keep entrypoints thin
- route startup through `RuntimeContext` + `bootstrap_runtime_mode(...)`
- route operator/chat startup truth through `build_runtime_backbone(...)`
- keep tool metadata behind explicit contracts
- keep provider-role routing behind `core/provider_routing.py` so local drones and higher-tier synthesis providers stay selectable without rewiring callers
- keep the main model execution router behind `core/memory_first_router.py` so chat/research synthesis can honor provider roles without leaking provider-selection policy into callers
- keep memory behind the `core.persistent_memory` facade and `core/memory/` internals
- keep agent-facing fast-path wrappers behind `core/agent_runtime/fast_path_facade.py`
- keep utility/date/smalltalk shortcut logic inside `core/agent_runtime/fast_paths.py`
- keep fresh-info, weather, news, and price lookup routing inside `core/agent_runtime/fast_live_info.py`
- keep public presence heartbeat, idle commons cadence, and autonomous Hive research loops inside `core/agent_runtime/presence.py`
- keep chat-surface wording, observation shaping, and Hive truth narration behind `core/agent_runtime/chat_surface.py` so user-facing wording changes stay local
- keep credit commands, capability/help truth, credit status rendering, and fast/action result shaping behind `core/agent_runtime/fast_command_surface.py` so command-surface changes stay local
- keep Hive topic/create/followup wrappers behind `core/agent_runtime/hive_topic_facade.py`, with create/confirm/public-safe copy logic in `core/agent_runtime/hive_topic_create.py`, mutation/update/delete logic in `core/agent_runtime/hive_topics.py`, research/status continuation logic in `core/agent_runtime/hive_research_followup.py`, and frontdoor/review/cleanup glue in `core/agent_runtime/hive_followups.py`
- keep builder workflow/scaffold wrappers behind `core/agent_runtime/builder_facade.py` and the real builder logic inside `core/agent_runtime/builder/`
- keep research/live-web/tool-loop wrappers behind `core/agent_runtime/research_tool_loop_facade.py` and the real tool execution contracts behind `core/tool_intent_executor.py`
- keep helper drone candidate fan-out behind `core/model_teacher_pipeline.py` so provider swarms stay bounded and policy-shaped instead of leaking into every caller
- keep dashboard routing behind `core.brain_hive_dashboard` and `core/dashboard/render.py`, with workstation state/render isolated in `core/dashboard/workstation_state.py`, `core/dashboard/workstation_render.py`, and `core/dashboard/workstation_client.py`
- keep public-hive auth/bootstrap behind `core.public_hive_bridge` facades and `core/public_hive/auth.py` internals
- keep feature/store/network-specific logic behind package boundaries
- prefer adapters/facades over direct rewrites of giant mixed modules

## What Still Needs Work

- split the largest mixed-responsibility runtime and dashboard modules
- reduce direct storage/bootstrap calls outside the canonical startup path
- make orchestration/task lifecycle more explicit and shared across surfaces
- keep the remaining agent orchestration, workstation browser runtime, public-feed, and Hive create/research-followup hotspots shrinking behind the new provider-role and runtime facades
- keep public/operator/web logic from bleeding into the runtime core
