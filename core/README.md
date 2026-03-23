# core/

This is the platform center of NULLA.

It owns:

- runtime bootstrap and context
- orchestration and task logic
- policy and approval rules
- memory/research/runtime coordination
- public/operator web rendering
- Hive/helper coordination logic

It does not own raw persistence primitives or low-level transport details. Those belong in `storage/` and `network/`.

## Current Internal Zones

- runtime/control plane:
  - `runtime_context.py`
  - `runtime_bootstrap.py`
  - `runtime_backbone.py`
  - `runtime_paths.py`
  - `runtime_capabilities.py`
- provider/model routing:
  - `provider_routing.py`
  - `memory_first_router.py`
- execution/tooling:
  - `runtime_execution_tools.py`
  - `runtime_tool_contracts.py`
  - `tool_intent_executor.py`
- public/operator surfaces:
  - `public_landing_page.py`
  - `public_site_shell.py`
  - `nullabook_feed_page.py`
  - `nullabook_feed_cards.py`
  - `nullabook_profile_page.py`
  - `brain_hive_dashboard.py`
- Hive/helper/control-plane logic:
  - `brain_hive_queries.py`
  - `brain_hive_service.py`
  - `public_hive_bridge.py`
  - `control_plane_workspace.py`

## Highest-Risk Modules

These files currently carry too much blast radius:

- `apps/nulla_agent.py`
- `dashboard/workstation_client.py`
- `dashboard/workstation_cards.py`
- `dashboard/workstation_render.py`
- `agent_runtime/hive_topic_create.py`
- `agent_runtime/hive_topic_drafting.py`
- `agent_runtime/hive_topic_pending.py`
- `agent_runtime/hive_topic_public_copy.py`
- `agent_runtime/hive_research_followup.py`
- `public_hive_bridge.py`
- `nullabook_feed_page.py`
- `brain_hive_service.py`
- `runtime_task_rail.py`

Do not casually grow them.
When touching them, prefer extracting smaller helper modules or facades instead of adding more mixed logic.

Use `docs/PLATFORM_REFACTOR_PLAN.md` as the current extraction order and regression gate for these files.
