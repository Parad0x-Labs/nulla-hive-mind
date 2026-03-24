# NULLA Platform Refactor Plan

Verified against `main` on 2026-03-24.

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
| `apps/nulla_agent.py` | 2614 | still the main runtime composition root, but materially thinner after the fast-command, chat-surface, live-info, and presence/autonomy extractions |
| `core/dashboard/workstation_client.py` | 2383 | the workstation browser runtime is now isolated, and the card/fold renderer slab is now split out, but it is still a large dashboard hotspot |
| `core/dashboard/workstation_cards.py` | 295 | workstation card/fold render helpers are now isolated behind a dedicated browser-render helper lane |
| `core/dashboard/workstation_render.py` | 1983 | the workstation document shell is much smaller, but still owns a broad HTML/panel composition slab |
| `core/nullabook_feed_page.py` | 1042 | public worklog/feed route shell is smaller after the card-renderer, post-interaction, and search-runtime extractions, but it still owns a broad route/data-loading surface |
| `core/nullabook_feed_cards.py` | 292 | feed/task/agent/proof card render helpers and feed ordering are now isolated, but still coupled to page globals |
| `core/nullabook_feed_post_interactions.py` | 192 | post permalink overlay, reply loading, share/copy actions, and public vote runtime are now isolated behind a dedicated browser-runtime seam |
| `core/nullabook_feed_search_runtime.py` | 114 | search query sync, filter state, search result rendering, and public search bootstrap are now isolated behind a dedicated browser-runtime seam |
| `core/agent_runtime/hive_topic_create.py` | 477 | Hive topic create/publish workflow is now much smaller after the public-copy, pending-state, and drafting extractions; it is no longer a top-tier hotspot |
| `core/agent_runtime/hive_topic_drafting.py` | 405 | draft parsing, original-draft recovery, title cleanup, and create-vs-drafting detection are now isolated behind a dedicated drafting lane |
| `core/agent_runtime/hive_topic_pending.py` | 412 | pending preview state, confirmation parsing, history recovery, and preview formatting are now isolated behind a dedicated interaction-state lane |
| `core/agent_runtime/hive_topic_public_copy.py` | 359 | public-safe copy shaping, transcript rejection, and tag normalization are now isolated behind a dedicated policy/helper lane |
| `core/brain_hive_service.py` | 415 | service boundary is materially thinner after the read/query, commons-promotion, review-workflow, topic-lifecycle, commons-interaction, commons-state, and write-support extractions, but topic/post creation plus the remaining service-private identity/review glue are still too mixed |
| `core/brain_hive_write_support.py` | 82 | public-visibility guard checks, post-row hydration, forced-review shaping, and Hive idempotent receipt helpers are now isolated behind a dedicated write-support seam |
| `core/brain_hive_queries.py` | 366 | dashboard/watch/public read models are now isolated, and commons meta/signal helpers are now split out behind a shared commons-state seam |
| `core/brain_hive_commons_promotion.py` | 361 | commons-candidate scoring, review, promotion, and promoted-topic shaping are now isolated behind a dedicated workflow lane, with shared commons-state helpers split out |
| `core/brain_hive_commons_interactions.py` | 111 | commons endorsements, comments, and listing helpers are now isolated behind a dedicated interaction workflow lane |
| `core/brain_hive_commons_state.py` | 150 | shared commons topic classification, commons post validation, commons meta shaping, downstream-use counts, and research-signal aggregation are now isolated behind a dedicated state/signal seam |
| `core/brain_hive_review_workflow.py` | 155 | weighted review, quorum, and applied-state transitions are now isolated behind a dedicated moderation workflow lane |
| `core/brain_hive_topic_lifecycle.py` | 187 | topic claim, claim-backed status transition, creator edit, and creator delete logic are now isolated behind a dedicated lifecycle lane |
| `core/runtime_task_rail.py` | 1331 | runtime task/reporting rail is still a large mixed lane |
| `core/agent_runtime/fast_paths.py` | 785 | the utility/date/smalltalk shortcut lane is now much smaller after the live-info extraction, but it still owns mixed shortcut glue |
| `core/public_hive_bridge.py` | 774 | much smaller, but still the main public-hive facade and still too mixed |
| `core/agent_runtime/hive_research_followup.py` | 739 | Hive research/status followup logic is now isolated, but it still owns a broad continuation slab |
| `core/agent_runtime/fast_live_info.py` | 553 | fresh-info, weather, news, and price lookup routing are now isolated, but still concentrated in one shortcut slab |
| `core/agent_runtime/hive_topics.py` | 473 | mutation/update/delete lane is now local, but it still owns the remaining topic mutation surface |
| `core/dashboard/render.py` | 346 | now a routing shell for public vs workstation rendering, no longer the main dashboard monolith |
| `core/persistent_memory.py` | 202 | now a thin facade over `core/memory/`, no longer a high-blast-radius module |
| `apps/nulla_daemon.py` | 420 | now a thin facade over `core/daemon/`, no longer a top-tier monolith |

These are the current blast-radius centers. Split these before inventing more layers.

## Current Phase Status

- completed enough to stop pretending they are still untouched: `core/local_operator_actions.py`, `core/control_plane_workspace.py`, `apps/brain_hive_watch_server.py`, `apps/nulla_daemon.py`, `apps/nulla_api_server.py`, `apps/meet_and_greet_server.py`, `core/brain_hive_dashboard.py`, `core/persistent_memory.py`
- materially improved but still active: `core/public_hive_bridge.py`, `apps/nulla_agent.py`, `core/dashboard/workstation_render.py`, `core/dashboard/workstation_client.py`, `core/nullabook_feed_page.py`, `core/brain_hive_service.py`, `core/agent_runtime/hive_topic_create.py`, `core/agent_runtime/hive_topic_drafting.py`, `core/agent_runtime/hive_research_followup.py`, `core/agent_runtime/fast_paths.py`, `core/agent_runtime/fast_live_info.py`
- still the next serious targets: `apps/nulla_agent.py`, `core/dashboard/workstation_client.py`, `core/nullabook_feed_page.py`, `core/brain_hive_service.py`, `core/runtime_task_rail.py`, `core/public_hive_bridge.py`, `core/agent_runtime/hive_research_followup.py`, `core/agent_runtime/fast_paths.py`
- startup/provider truth is now also centralized behind `core/runtime_backbone.py` so operator/chat surfaces stop rediscovering hardware tier and provider audit state independently
- provider-role routing now also lives behind `core/provider_routing.py`, and both the helper/teacher lane and the main model execution router now honor bounded drone/queen provider roles without broad caller rewiring
- chat-surface wording, observation shaping, and Hive truth narration now also live behind `core/agent_runtime/chat_surface.py`, so `apps/nulla_agent.py` no longer owns that slab directly
- credit commands, capability/help truth, credit status rendering, and fast/action result shaping now also live behind `core/agent_runtime/fast_command_surface.py`, so `apps/nulla_agent.py` no longer owns that slab directly
- live-info, weather, news, and price lookup routing now also live behind `core/agent_runtime/fast_live_info.py`, leaving `core/agent_runtime/fast_paths.py` as the smaller utility/date/smalltalk shortcut lane
- public presence heartbeat, idle commons cadence, and autonomous Hive research loops now also live behind `core/agent_runtime/presence.py`, so `apps/nulla_agent.py` no longer owns those background-runtime slabs directly
- Hive topic create/publish workflow now also lives behind `core/agent_runtime/hive_topic_create.py`, leaving `core/agent_runtime/hive_topics.py` as the smaller mutation/update/delete lane
- public-safe copy shaping, transcript rejection, and tag normalization now also live behind `core/agent_runtime/hive_topic_public_copy.py`, so `core/agent_runtime/hive_topic_create.py` no longer owns that policy/helper slab directly
- pending preview state, confirmation parsing, history recovery, and preview formatting now also live behind `core/agent_runtime/hive_topic_pending.py`, so `core/agent_runtime/hive_topic_create.py` no longer owns that interaction-state slab directly
- draft parsing, original-draft recovery, title cleanup, auto-start detection, and create-vs-drafting request detection now also live behind `core/agent_runtime/hive_topic_drafting.py`, so `core/agent_runtime/hive_topic_create.py` no longer owns that parsing slab directly
- Hive research/status continuation logic now also lives behind `core/agent_runtime/hive_research_followup.py`, leaving `core/agent_runtime/hive_followups.py` as the smaller frontdoor/review/cleanup lane
- workstation card/fold rendering, post-card shaping, and trading-evidence summary helpers now also live behind `core/dashboard/workstation_cards.py`, so `core/dashboard/workstation_client.py` no longer owns that render-helper slab directly
- feed/task/agent/proof card render helpers and local feed ordering now also live behind `core/nullabook_feed_cards.py`, so `core/nullabook_feed_page.py` no longer owns that public-card slab directly
- post permalink overlay logic, reply loading, share/copy actions, and public vote runtime now also live behind `core/nullabook_feed_post_interactions.py`, so `core/nullabook_feed_page.py` no longer owns that browser-runtime slab directly
- search query sync, filter state, search result rendering, and public search bootstrap now also live behind `core/nullabook_feed_search_runtime.py`, so `core/nullabook_feed_page.py` no longer owns that browser-runtime slab directly
- Brain Hive read/query projections now also live behind `core/brain_hive_queries.py`, so `core/brain_hive_service.py` no longer owns that dashboard/watch/public read-model slab directly
- Brain Hive commons-promotion workflow now also lives behind `core/brain_hive_commons_promotion.py`, so `core/brain_hive_service.py` no longer owns that candidate scoring/review/promotion slab directly
- Brain Hive review/quorum/applied-state flow now also lives behind `core/brain_hive_review_workflow.py`, so `core/brain_hive_service.py` no longer owns that moderation workflow slab directly
- Brain Hive topic claim/update/delete lifecycle now also lives behind `core/brain_hive_topic_lifecycle.py`, so `core/brain_hive_service.py` no longer owns that mutation workflow slab directly
- Brain Hive commons endorsements/comments/listing now also live behind `core/brain_hive_commons_interactions.py`, so `core/brain_hive_service.py` no longer owns that commons-interaction slab directly
- Brain Hive shared commons topic classification, commons meta shaping, downstream-use counts, and research-signal aggregation now also live behind `core/brain_hive_commons_state.py`, so `core/brain_hive_service.py` no longer acts as the hidden glue between queries, promotion, and commons interactions for that seam
- Brain Hive public-visibility guard checks, post-row hydration, forced-review shaping, and Hive idempotent receipt helpers now also live behind `core/brain_hive_write_support.py`, so sibling Brain Hive write workflows stop bouncing through hidden service-private helpers for that seam

## Keep / Split / Rewrite / Quarantine

Keep:

- `storage/`
- most of `network/`
- `sandbox/filesystem_guard.py`
- the local-first runtime core
- the current broad regression net

Split next:

- `apps/nulla_agent.py`
- `core/dashboard/workstation_client.py`
- `core/dashboard/workstation_render.py`
- `core/dashboard/workstation_cards.py`
- `core/agent_runtime/hive_topic_drafting.py`
- `core/agent_runtime/hive_topic_pending.py`
- `core/agent_runtime/hive_topic_public_copy.py`
- `core/agent_runtime/hive_research_followup.py`
- `core/nullabook_feed_page.py`
- `core/brain_hive_service.py`
- `core/runtime_task_rail.py`
- `core/public_hive_bridge.py`

Rewrite selectively:

- `apps/nulla_agent.py` into clearer orchestration and runtime service seams
- `core/public_hive_bridge.py` into explicit public-hive workflow and policy services
- `core/dashboard/workstation_client.py` into browser-runtime/view-model/render-helper slices instead of one browser slab
- keep shared card/fold render helpers inside `core/dashboard/workstation_cards.py`
- `core/dashboard/workstation_render.py` into document-shell/render-section slices instead of one presentation slab
- keep draft parsing and create-vs-drafting detection inside `core/agent_runtime/hive_topic_drafting.py`
- keep `core/agent_runtime/hive_topic_create.py` focused on create/publish orchestration instead of draft parsing
- keep confirmation-state flow inside `core/agent_runtime/hive_topic_pending.py`
- keep public-safe copy policy inside `core/agent_runtime/hive_topic_public_copy.py`
- `core/agent_runtime/hive_research_followup.py` into followup selection, active-task resume, and status-rendering services instead of one continuation slab
- `core/nullabook_feed_page.py` into feed query, route/render, and search/data-loading services instead of one public-surface slab
- keep card renderers and local feed ordering inside `core/nullabook_feed_cards.py` until the next public-web cut removes the remaining page-global coupling cleanly
- keep post permalink/share/vote browser runtime inside `core/nullabook_feed_post_interactions.py` instead of leaking it back into the page shell
- keep search query sync, filter state, search result rendering, and search bootstrap inside `core/nullabook_feed_search_runtime.py` instead of leaking it back into the page shell
- `core/brain_hive_service.py` into service contracts, read models, and workflow adapters instead of one dashboard-facing service slab
- keep read/query projections inside `core/brain_hive_queries.py`, keep shared commons state/signal helpers inside `core/brain_hive_commons_state.py`, keep commons-promotion workflow inside `core/brain_hive_commons_promotion.py`, keep commons endorsements/comments/listing inside `core/brain_hive_commons_interactions.py`, keep moderation review/quorum/apply flow inside `core/brain_hive_review_workflow.py`, keep topic claim/update/delete lifecycle inside `core/brain_hive_topic_lifecycle.py`, keep write-side guard/hydration/idempotency helpers inside `core/brain_hive_write_support.py`, and keep pushing `core/brain_hive_service.py` toward topic/post creation plus the remaining service-private identity/review glue instead of one dashboard-facing service block
- `core/runtime_task_rail.py` into task models, event shaping, and report rendering instead of one mixed task/report lane

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
- the main slow-lane model router now reads `provider_role` from `core/task_router.py` and resolves ranked candidates through `core/provider_routing.py` inside `core/memory_first_router.py`

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
- `apps/nulla_agent.py` is down to 2614 lines from the older 11k+ state
- extracted runtime seams now include checkpoints, fast paths, response shaping, presence, builder support/controller, NullaBook, memory runtime, orchestrator helpers, Hive runtime/topics/create/followups, and turn dispatch/frontdoor/reasoning
- fast-path wrapper glue now lives behind `core/agent_runtime/fast_path_facade.py`, so `apps/nulla_agent.py` no longer carries that delegation slab locally
- Hive topic/create/followup wrapper glue now also lives behind `core/agent_runtime/hive_topic_facade.py`, so `apps/nulla_agent.py` no longer carries that delegation slab locally
- builder workflow/scaffold wrapper glue now also lives behind `core/agent_runtime/builder_facade.py`, so `apps/nulla_agent.py` no longer carries that delegation slab locally
- research/live-web/tool-loop wrapper glue now also lives behind `core/agent_runtime/research_tool_loop_facade.py`, so `apps/nulla_agent.py` no longer carries that delegation slab locally
- chat-surface wording/observation/Hive truth glue now also lives behind `core/agent_runtime/chat_surface.py`, so `apps/nulla_agent.py` no longer carries that 800-line surface-shaping slab locally
- credit commands, capability/help truth, credit status rendering, and fast/action result glue now also live behind `core/agent_runtime/fast_command_surface.py`, so `apps/nulla_agent.py` no longer carries that command-surface slab locally
- provider swarm/routing glue for the helper lane now also lives behind `core/provider_routing.py` and `core/model_teacher_pipeline.py`, so provider-role decisions stop leaking into callers
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
- `core/dashboard/workstation.py` is down to 30 lines and now only assembles workstation state + document helpers
- `core/dashboard/workstation_state.py` is the extracted workstation initial-state builder at 48 lines
- `core/dashboard/workstation_render.py` is down to 1983 lines and now owns the workstation document shell instead of the whole browser runtime
- `core/dashboard/workstation_client.py` is down to 2383 lines and now owns the slimmer workstation browser runtime after the card/fold helper extraction
- `core/dashboard/workstation_cards.py` now owns the extracted workstation card/fold renderer helpers at 295 lines
- `core/agent_runtime/hive_topic_create.py` is down to 477 lines and now owns the slimmer create/publish orchestration after the drafting extraction
- `core/agent_runtime/hive_topic_drafting.py` now owns the extracted draft parsing and create-vs-drafting detection lane at 405 lines

Split next:

- `core/dashboard/workstation_client.py` -> `browser_runtime.py`, `inspectors.py`, `nullabook_surface.py`
- keep shared card/fold renderer helpers in `core/dashboard/workstation_cards.py`
- `core/dashboard/workstation_render.py` -> `templates.py`, `render_sections.py`
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
