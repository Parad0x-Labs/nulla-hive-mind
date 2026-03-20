# NULLA Platform Refactor Plan

Verified against `main` on 2026-03-20.

This is not a rewrite fantasy. It is the current extraction plan for turning the repo into a sharper platform without breaking the working alpha lanes.

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

| File | Lines |
|------|-------|
| `apps/nulla_agent.py` | 11437 |
| `core/brain_hive_dashboard.py` | 5917 |
| `core/tool_intent_executor.py` | 2721 |
| `core/public_hive_bridge.py` | 2200 |
| `core/local_operator_actions.py` | 1630 |
| `core/control_plane_workspace.py` | 1316 |
| `apps/nulla_api_server.py` | 941 |
| `apps/brain_hive_watch_server.py` | 868 |

These are the current blast-radius centers. Split these before inventing more layers.

## Keep / Split / Rewrite / Quarantine

Keep:

- `storage/`
- most of `network/`
- `sandbox/filesystem_guard.py`
- the local-first runtime core
- the current broad regression net

Split next:

- `apps/nulla_agent.py`
- `core/tool_intent_executor.py`
- `core/public_hive_bridge.py`
- `core/local_operator_actions.py`
- `core/control_plane_workspace.py`
- `core/brain_hive_dashboard.py`

Rewrite selectively:

- `apps/nulla_api_server.py` into route modules first, ASGI later
- `apps/brain_hive_watch_server.py` into watch-route/cache/TLS modules first

Quarantine in narrative and architecture priority:

- settlement / token / DEX / marketplace layers
- anything that reads broader than the current proof path

## Phase Order

### Phase 1 - Extract `core/execution/` from `core/tool_intent_executor.py`

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

Move:

- `PublicHiveBridgeConfig`
- topic lifecycle methods
- publish/update/result methods
- profile sync methods
- client/auth helpers

Hard boundary:

- all outbound public writes must flow through `core/public_hive/privacy.py`

Keep `core/public_hive_bridge.py` as the stable facade for one release.

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

Target packages:

- `core/runtime/`
- `core/conversation/`
- `core/memory/`
- `core/execution/`
- `core/public_hive/`

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

Split next:

- `core/brain_hive_dashboard.py` -> `core/dashboard/queries.py`, `view_models.py`, `templates.py`, `render.py`
- `apps/brain_hive_watch_server.py` -> `core/web/watch/routes_public.py`, `routes_topic.py`, `cache.py`, `tls.py`, `responses.py`
- `apps/nulla_api_server.py` -> `core/web/api/routes_runtime.py`, `routes_tools.py`, `routes_hive.py`, `routes_health.py`, `auth.py`, `streaming.py`, `responses.py`

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
