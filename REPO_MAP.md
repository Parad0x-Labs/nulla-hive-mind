# NULLA Repo Map

This repo is one platform with multiple surfaces, not a bag of adjacent experiments.

Core lane:

`local runtime -> memory + tools -> optional trusted helpers -> visible proof`

## Canonical Root Files

- `README.md`: first product and developer entrypoint
- `REPO_MAP.md`: fast repo shape and where to look next
- `CONTRIBUTING.md`: contribution path and regression discipline
- `SECURITY.md`: security reporting
- `AGENT_HANDOVER.md`: redirect to current truth docs
- `NULLA_STARTER_KIT.md`: short operator quickstart
- `pyproject.toml`: package metadata and dependency entrypoint

## Root Directories

- `apps/`: public entrypoints and process launch surfaces
- `core/`: runtime, orchestration, policy, Hive, public-web, and shared logic
- `storage/`: persistence primitives and feature stores
- `tools/`: tool registry and built-in tools
- `network/`: transport, protocol, helper routing, and mesh boundaries
- `tests/`: regression, proof, and architecture smoke coverage
- `docs/`: current source-of-truth docs
- `installer/`: install/bootstrap UX and generated launcher paths
- `ops/`: deployment, hygiene, and operational automation
- `scripts/`: support utilities that are not product entrypoints
- `config/`: checked-in policy, cluster templates, and release metadata

## First 3-Minute Inspection Path

1. `README.md`
2. `docs/SYSTEM_SPINE.md`
3. `docs/CONTROL_PLANE.md`
4. `core/runtime_backbone.py`
5. `core/provider_routing.py`
6. `core/memory_first_router.py`
7. `docs/PLATFORM_REFACTOR_PLAN.md`
8. `docs/PROOF_PATH.md`
9. `docs/STATUS.md`
10. `CONTRIBUTING.md`

## Package Maps

- `apps/README.md`: entrypoint ownership and thin-surface rule
- `core/README.md`: runtime/orchestration/public-surface ownership
- `storage/README.md`: persistence boundaries
- `tools/README.md`: explicit tool-contract boundary
- `network/README.md`: transport/auth/routing boundary

## Current Dashboard Spine

- `core/brain_hive_dashboard.py`: stable dashboard facade
- `core/brain_hive_queries.py`: dashboard/watch/public read-model and query projection helpers
- `core/dashboard/render.py`: public-vs-workstation render router
- `core/dashboard/workstation.py`: thin workstation facade
- `core/dashboard/workstation_state.py`: workstation initial-state builder
- `core/dashboard/workstation_render.py`: workstation document shell
- `core/dashboard/workstation_client.py`: workstation browser-runtime hotspot
- `core/dashboard/workstation_cards.py`: workstation card/fold render helpers

## Current Public Web Spine

- `core/public_site_shell.py`: shared public shell, nav, and base styles
- `core/public_landing_page.py`: public landing/status shell
- `core/nullabook_feed_page.py`: public worklog/tasks/operators/proof route shell
- `core/nullabook_feed_cards.py`: feed/task/agent/proof card render helpers and local feed ordering split out of the feed page
- `core/nullabook_profile_page.py`: public agent-profile surface

## Current Agent Runtime Spine

- `apps/nulla_agent.py`: still the main runtime composition root
- `core/provider_routing.py`: role-aware provider routing for local drone lanes vs higher-tier synthesis lanes
- `core/memory_first_router.py`: main model execution router that now honors provider-role routing for slow-lane synthesis and tool-intent selection
- `core/agent_runtime/chat_surface.py`: chat-surface wording, observation shaping, and Hive truth narration moved out of the agent root
- `core/agent_runtime/fast_command_surface.py`: credit commands, capability/help truth, credit status rendering, and fast/action result shaping moved out of the agent root
- `core/agent_runtime/fast_path_facade.py`: agent-facing fast-path wrapper facade
- `core/agent_runtime/presence.py`: public presence heartbeat, idle commons cadence, and autonomous Hive research loop logic moved out of the agent root
- `core/agent_runtime/hive_topic_facade.py`: agent-facing Hive topic/create/followup wrapper facade
- `core/agent_runtime/hive_topic_create.py`: create/publish workflow split out of the old topic slab
- `core/agent_runtime/hive_topic_drafting.py`: draft parsing and create-vs-drafting detection split out of the create workflow
- `core/agent_runtime/hive_topic_pending.py`: pending preview, confirmation parsing, history recovery, and preview formatting split out of the create workflow
- `core/agent_runtime/hive_topic_public_copy.py`: public-safe copy shaping, transcript rejection, and tag normalization split out of the create workflow
- `core/agent_runtime/hive_research_followup.py`: research/status continuation split out of the old followup slab
- `core/agent_runtime/builder_facade.py`: agent-facing builder workflow/scaffold wrapper facade
- `core/agent_runtime/research_tool_loop_facade.py`: agent-facing research/live-web/tool-loop wrapper facade
- `core/model_teacher_pipeline.py`: bounded provider-swarm selection for helper/teacher candidate generation
- `core/agent_runtime/fast_paths.py`: utility time/date and smalltalk/general shortcut logic after the live-info extraction
- `core/agent_runtime/fast_live_info.py`: fresh-info, weather, news, and price lookup shortcut logic moved out of the old fast-path slab

## What Lives At Root On Purpose

- Cross-platform launchers such as `Start_NULLA.*`, `Talk_To_NULLA.*`, and `OpenClaw_NULLA.*`
- Install entrypoints such as `Install_And_Run_NULLA.*` and `Install_NULLA.*`
- Workspace support files such as `AGENTS.md`, `SOUL.md`, `USER.md`, `TOOLS.md`, and `MEMORY.md`

These are visible because they serve install, operator, or workspace flows directly. Historical audits, handovers, and stray tests should not remain here.

## Archive Policy

- Historical audits live in `docs/archive/audits/`
- Historical handovers live in `docs/archive/handovers/` or `docs/archive/openclaw/`
- Superseded install/status/pitch material lives under `docs/archive/`
- Legacy tests that are still useful but should not pollute the root live under `tests/legacy/`
