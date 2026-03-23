# NULLA Platform Refactor Plan

Verified against `main` on 2026-03-22.

This is not a rewrite fantasy. It is the current extraction plan for turning the repo into a sharper platform without breaking the working alpha lanes.

This doc used to undersell the current trunk because the line-count snapshot was stale. It now reflects the real blast-radius map on `main`, not the older pre-extraction numbers.

The rule for every phase:

- keep the local runtime as the product center
- reduce blast radius instead of adding more mixed logic
- preserve behavior through facades and shims where needed
- run cumulative regression at each step

## Why This Exists

NULLA already has the right system spine:

`local runtime -> memory + tools -> optional trusted helpers -> visible proof`

The repo shape is still carrying too much risk in a small set of giant files. The goal of this plan is to lower change risk without pretending the platform needs a ground-up rewrite.

## Verified Current Risk Snapshot

The biggest files on the current trunk are:

| File | Lines | Current reality |
|------|-------|-----------------|
| `apps/nulla_agent.py` | 5632 | still the main runtime monolith and the largest remaining blast-radius center |
| `core/dashboard/workstation.py` | 4693 | the workstation dashboard rendering slab is now the real dashboard hotspot |
| `core/agent_runtime/hive_topics.py` | 2038 | Hive topic creation/update/delete logic is still too concentrated |
| `core/nullabook_feed_page.py` | 1627 | public worklog/feed surface is still too broad |
| `core/brain_hive_service.py` | 1353 | service boundary exists, but it still owns too much dashboard-facing behavior |
| `core/runtime_task_rail.py` | 1331 | runtime task/reporting rail is still a large mixed lane |
| `core/agent_runtime/fast_paths.py` | 1314 | too many chat/runtime shortcuts still live together |
| `core/public_hive_bridge.py` | 774 | much smaller, but still the main public-hive facade and still too mixed |
| `core/dashboard/render.py` | 346 | now a routing shell for public vs workstation rendering, no longer the main dashboard monolith |
| `core/persistent_memory.py` | 202 | now a thin facade over `core/memory/`, no longer a high-blast-radius module |
| `apps/nulla_daemon.py` | 420 | now a thin facade over `core/daemon/`, no longer a top-tier monolith |

These are the current blast-radius centers. Split these before inventing more layers.

## Current Phase Status

- completed enough to stop pretending they are still untouched: `core/local_operator_actions.py`, `core/control_plane_workspace.py`, `apps/brain_hive_watch_server.py`, `apps/nulla_daemon.py`, `apps/nulla_api_server.py`, `apps/meet_and_greet_server.py`, `core/brain_hive_dashboard.py`, `core/persistent_memory.py`
- materially improved but still active: `core/public_hive_bridge.py`, `apps/nulla_agent.py`, `core/dashboard/workstation.py`, `core/agent_runtime/hive_topics.py`, `core/agent_runtime/fast_paths.py`
- still the next serious targets: `apps/nulla_agent.py`, `core/dashboard/workstation.py`, `core/public_hive_bridge.py`, `core/agent_runtime/hive_topics.py`, `core/agent_runtime/fast_paths.py`
- startup/provider truth is now also centralized behind `core/runtime_backbone.py` so operator/chat surfaces stop rediscovering hardware tier and provider audit state independently

## Keep / Split / Rewrite / Quarantine

Keep:

- `storage/`
- most of `network/`
- `sandbox/filesystem_guard.py`
- the local-first runtime core
- the current broad regression net

Split next:

- `core/brain_hive_dashboard.py`
- `apps/nulla_agent.py`
- `core/dashboard/workstation.py`
- `core/agent_runtime/hive_topics.py`
- `core/agent_runtime/fast_paths.py`
- `core/tool_intent_executor.py`
- `core/public_hive_bridge.py`

Rewrite selectively:

- `apps/nulla_agent.py` into clearer orchestration and runtime service seams
- `core/public_hive_bridge.py` into explicit public-hive workflow and policy services
- `core/dashboard/workstation.py` into query/view-model/render slices instead of one presentation slab

Quarantine in narrative and architecture priority:

- settlement / token / DEX / marketplace layers
- anything that reads broader than the current proof path

## Phase Order

### Phase 1 - Extract `core/execution/` from `core/tool_intent_executor.py`

Status on trunk:

- `core/execution/` is already live with planner, models, receipts, web tools, and Hive tools
- `core/tool_intent_executor.py` is down to 446 lines
- this is no longer a top-tier monolith; keep the facade thin and stop letting new execution concerns leak back in

Create:

- `core/execution/__init__.py`
- `core/execution/capability_registry.py`
- `core/execution/capability_truth.py`
- `core/execution/policy.py`
- `core/execution/planner.py`
- `core/execution/dispatcher.py`
- `core/execution/render.py`

Move:

- `runtime_capability_ledger()`
- `supported_public_capability_tags()`
- `capability_truth_for_request()`
- `should_attempt_tool_intent()`
- `plan_tool_workflow()`
- `execute_tool_intent()`
- `render_capability_truth_response()`

Keep `core/tool_intent_executor.py` as a shim for one release.

Targeted regression:

```bash
pytest -q \
  tests/test_tool_intent_executor.py \
  tests/test_runtime_capability_ledger.py \
  tests/test_runtime_tool_registry_contract.py \
  tests/test_tool_registry_contracts.py \
  tests/test_runtime_execution_tools.py
```

Cumulative gate:

```bash
pytest -q \
  tests/test_tool_intent_executor.py \
  tests/test_runtime_capability_ledger.py \
  tests/test_runtime_tool_registry_contract.py \
  tests/test_tool_registry_contracts.py \
  tests/test_runtime_execution_tools.py \
  tests/test_nulla_runtime_contracts.py \
  tests/test_openclaw_tooling_context.py
```

### Phase 2 - Split `core/local_operator_actions.py` into `core/operator/`

Status on trunk:

- `core/operator/` is already live with models, parser, registry, approvals, handlers, and storage helpers
- `core/local_operator_actions.py` is down to 392 lines
- this is no longer a top-tier monolith unless new work starts growing the facade again

Create:

- `core/operator/__init__.py`
- `core/operator/models.py`
- `core/operator/parser.py`
- `core/operator/dispatch.py`
- `core/operator/registry.py`
- `core/operator/guardrails.py`
- `core/operator/handlers/__init__.py`
- `core/operator/handlers/calendar.py`
- `core/operator/handlers/filesystem.py`
- `core/operator/handlers/processes.py`
- `core/operator/handlers/system.py`

Move:

- `OperatorActionIntent`
- `parse_operator_action_intent()`
- `dispatch_operator_action()`

Keep `core/local_operator_actions.py` as a shim for one release.

Targeted regression:

```bash
pytest -q \
  tests/test_operator_actions.py \
  tests/test_runtime_execution_tools.py \
  tests/test_nulla_runtime_contracts.py
```

Cumulative gate:

```bash
pytest -q \
  tests/test_tool_intent_executor.py \
  tests/test_runtime_execution_tools.py \
  tests/test_operator_actions.py \
  tests/test_nulla_runtime_contracts.py \
  tests/test_openclaw_tooling_context.py
```

### Phase 3 - Refactor `core/public_hive_bridge.py` into `core/public_hive/`

Status on trunk:

- `core/public_hive/__init__.py`, `auth.py`, `bootstrap.py`, `client.py`, `config.py`, `truth.py`, `topic_writes.py`, `publication.py`, and `moderation.py` are already live
- `core/public_hive_bridge.py` is down to 774 lines
- `core/public_hive/writes.py` is down to a 37-line facade
- auth/bootstrap/config composition now lives behind `core/public_hive/auth.py`
- topic/profile/read/privacy boundaries are still the remaining cleanup surface

Create:

- `core/public_hive/__init__.py`
- `core/public_hive/config.py`
- `core/public_hive/client.py`
- `core/public_hive/auth.py`
- `core/public_hive/privacy.py`
- `core/public_hive/topic_service.py`
- `core/public_hive/publish_service.py`
- `core/public_hive/profile_service.py`
- `core/public_hive/bootstrap.py`
- `core/public_hive/topic_writes.py`
- `core/public_hive/publication.py`
- `core/public_hive/moderation.py`

Move:

- `PublicHiveBridgeConfig`
- topic lifecycle methods
- publish/update/result methods
- profile sync methods
- client/auth helpers

Hard boundary:

- all outbound public writes must flow through `core/public_hive/privacy.py`

Keep `core/public_hive_bridge.py` as the stable facade for one release.
Keep `core/public_hive/writes.py` as the stable write-facade for one release.

Targeted regression:

```bash
pytest -q \
  tests/test_public_hive_bridge.py \
  tests/test_brain_hive_service.py \
  tests/test_nullabook_api.py \
  tests/test_meet_and_greet_service.py
```

Cumulative gate:

```bash
pytest -q \
  tests/test_public_hive_bridge.py \
  tests/test_brain_hive_service.py \
  tests/test_nullabook_api.py \
  tests/test_meet_and_greet_service.py \
  tests/test_nullabook_feed_page.py \
  tests/test_nullabook_profile_page.py \
  tests/test_brain_hive_watch_server.py \
  tests/test_public_web_browser_smoke.py
```

### Phase 4 - Thin `apps/nulla_agent.py` into a composition root

Status on trunk:

- this phase is actively in progress, not hypothetical
- `apps/nulla_agent.py` is down to 5632 lines from the older 11k+ state
- extracted runtime seams now include checkpoints, fast paths, response shaping, presence, builder support/controller, NullaBook, memory runtime, orchestrator helpers, Hive runtime/topics/followups, and turn dispatch/frontdoor/reasoning
- the file is still too large, but the old doc numbers are no longer true

Target packages:

- `core/runtime/`
- `core/conversation/`
- `core/memory/`
- `core/execution/`
- `core/public_hive/`

What is already true on trunk:

- `core/persistent_memory.py` is down to 202 lines and now acts as the stable facade over `core/memory/files.py`, `policies.py`, `entries.py`, and `learning.py`
- API, meet, daemon, and watch entrypoints are already thin facades or ASGI-backed service roots
- the remaining work is concentrated inside the agent runtime and the large dashboard/public-surface modules, not the old entry shells

Move out of `apps/nulla_agent.py`:

- bootstrap wiring into `core/runtime/bootstrap.py`
- lifecycle into `core/runtime/lifecycle.py`
- background loops into `core/runtime/background_loops.py`
- turn execution into `core/conversation/turn_engine.py`
- context/retrieval wiring into `core/memory/context_loader.py` and `core/memory/router.py`

Targeted regression:

```bash
pytest -q \
  tests/test_nulla_runtime_contracts.py \
  tests/test_nulla_router_and_state_machine.py \
  tests/test_runtime_continuity.py \
  tests/test_tiered_context_loader.py \
  tests/test_entrypoints.py
```

Cumulative gate:

```bash
pytest -q \
  tests/test_runtime_context.py \
  tests/test_runtime_bootstrap.py \
  tests/test_startup_control_plane.py \
  tests/test_nulla_runtime_contracts.py \
  tests/test_nulla_router_and_state_machine.py \
  tests/test_runtime_continuity.py \
  tests/test_openclaw_tooling_context.py \
  tests/test_alpha_semantic_context_smoke.py
```

### Phase 5 - Split dashboard and web-server surfaces

Status on trunk:

- `apps/brain_hive_watch_server.py` is already thin and backed by `core/web/watch/`
- `apps/nulla_api_server.py` and `apps/meet_and_greet_server.py` are already thin facades
- `core/brain_hive_dashboard.py` is down to 156 lines and now fronts `core/dashboard/`
- `core/dashboard/render.py` is down to 346 lines and now routes public vs workstation rendering
- the real dashboard hotspot is now `core/dashboard/workstation.py` at 4693 lines

Split next:

- `core/dashboard/workstation.py` -> `queries.py`, `view_models.py`, `templates.py`, `render_sections.py`
- `apps/brain_hive_watch_server.py` -> `core/web/watch/routes_public.py`, `routes_topic.py`, `cache.py`, `tls.py`, `responses.py`
- keep `apps/nulla_api_server.py` and `apps/meet_and_greet_server.py` thin; do not re-bloat the facades

Targeted regression:

```bash
pytest -q \
  tests/test_brain_hive_dashboard.py \
  tests/test_brain_hive_watch_server.py \
  tests/test_public_landing_page.py \
  tests/test_public_web_browser_smoke.py \
  tests/test_nulla_api_server.py
```

Cumulative gate:

```bash
pytest -q \
  tests/test_brain_hive_dashboard.py \
  tests/test_brain_hive_watch_server.py \
  tests/test_public_landing_page.py \
  tests/test_public_web_browser_smoke.py \
  tests/test_nulla_api_server.py \
  tests/test_meet_and_greet_service.py \
  tests/test_nullabook_feed_page.py \
  tests/test_nullabook_profile_page.py
```

### Phase 6 - Split `core/control_plane_workspace.py`

Status on trunk:

- `core/control_plane/metrics_views.py`, `policies.py`, `queue_views.py`, `runtime_views.py`, `schemas.py`, and `templates.py` are already live
- `core/control_plane_workspace.py` is down to 558 lines
- this phase is mostly complete; only finish deeper repo/sync separation if the file starts re-coupling again

Create:

- `core/control_plane/workspace_paths.py`
- `core/control_plane/workspace_repo.py`
- `core/control_plane/workspace_sync.py`
- `core/control_plane/queue_views.py`
- `core/control_plane/proof_views.py`
- `core/control_plane/adaptation_views.py`

Rule:

- `workspace_sync.py` may mutate files
- `*_views.py` may query and shape data
- `workspace_repo.py` is the storage boundary

Targeted regression:

```bash
pytest -q \
  tests/test_control_plane_workspace.py \
  tests/test_startup_control_plane.py \
  tests/test_runtime_context.py \
  tests/test_runtime_bootstrap.py
```

Cumulative gate:

```bash
pytest -q \
  tests/test_control_plane_workspace.py \
  tests/test_startup_control_plane.py \
  tests/test_runtime_context.py \
  tests/test_runtime_bootstrap.py \
  tests/test_nulla_api_server.py \
  tests/test_brain_hive_watch_server.py
```

## Shared Refactor Rules

- Do not combine two blast-radius modules in one PR.
- Keep old import paths alive for one release when extracting hot paths.
- Move pure helpers first, then mutable/orchestration logic.
- Do not grow `brain_hive_dashboard.py`, `tool_intent_executor.py`, `public_hive_bridge.py`, `local_operator_actions.py`, or `control_plane_workspace.py` while their split PR is open.
- Public write privacy must stay fail-closed for public surfaces.
- Alpha honesty stays explicit in runtime behavior and docs.

## Full End Gate

Every phase closes with:

```bash
pytest tests/ -q
```

And, when relevant:

```bash
python3 ops/cumulative_stabilization.py --through G
```

If the phase touches public surfaces:

- run local browser smoke
- run live public smoke with disposable tags
- verify cleanup before calling the phase done

## Done Means

The plan is complete only when:

- the runtime center is clearer from imports alone
- the highest-risk files are materially smaller
- package boundaries are easier to reason about
- public proofs still work
- cumulative regression stays green at each phase

No big-bang rewrite. No “clean architecture” cosplay. Extract, prove, keep moving.
