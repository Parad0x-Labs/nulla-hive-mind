# NULLA Repo Map

This repo is one platform with multiple surfaces, not a bag of adjacent experiments.

Core lane:

`local runtime -> memory + tools -> optional trusted helpers -> visible proof`

## Canonical Root Files

- `README.md`: first product and developer entrypoint
- `REPO_MAP.md`: fast repo shape and where to look next
- `CONTRIBUTING.md`: contribution path and regression discipline
- `SECURITY.md`: security reporting
- `AGENT_HANDOVER.md`: redirect to the current operating docs
- `NULLA_STARTER_KIT.md`: short operator quickstart
- `pyproject.toml`: package metadata and dependency entrypoint

## Root Directories

- `apps/`: public entrypoints and process launch surfaces
- `core/`: runtime, orchestration, policy, Hive, public-web, and shared logic
- `storage/`: persistence primitives and feature stores
- `tools/`: tool registry and built-in tools
- `network/`: transport, protocol, helper routing, and mesh boundaries
- `tests/`: regression, proof, and architecture smoke coverage
- `docs/`: current source-of-record docs
- `installer/`: install/bootstrap UX and generated launcher paths
- `ops/`: deployment, hygiene, and operational automation
- `scripts/`: support utilities that are not product entrypoints
- `config/`: checked-in policy, cluster templates, and release metadata

## First 3-Minute Inspection Path

1. `README.md`
2. `docs/SYSTEM_SPINE.md`
3. `docs/CONTROL_PLANE.md`
4. `core/runtime_backbone.py`
5. `core/runtime_install_profiles.py`
6. `core/provider_routing.py`
7. `core/memory_first_router.py`
8. `docs/PLATFORM_REFACTOR_PLAN.md`
9. `docs/PROOF_PATH.md`
10. `docs/STATUS.md`
11. `CONTRIBUTING.md`

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
- `core/dashboard/workstation_render_tab_markup.py`: workstation tab navigation plus panel-markup seam
- `core/dashboard/workstation_render_styles.py`: workstation render-style aggregator seam
- `core/dashboard/workstation_render_shell_styles.py`: tiny shared-workstation style aggregator seam
- `core/dashboard/workstation_render_shell_primitives.py`: workstation shell reset/token CSS seam
- `core/dashboard/workstation_render_shell_components.py`: workstation shell component CSS seam
- `core/dashboard/workstation_render_shell_layout.py`: workstation shell layout/workbench CSS seam
- `core/dashboard/workstation_render_nullabook_styles.py`: tiny NullaBook-mode workstation style aggregator seam
- `core/dashboard/workstation_render_nullabook_content_styles.py`: embedded NullaBook content/feed CSS seam
- `core/dashboard/workstation_render_nullabook_mode_styles.py`: embedded NullaBook mode/state CSS seam
- `core/dashboard/workstation_render_nullabook_fabric_styles.py`: thin embedded-NullaBook fabric-style aggregator seam
- `core/dashboard/workstation_render_nullabook_fabric_telemetry_styles.py`: thin embedded-NullaBook telemetry-style aggregator over vitals and ticker CSS leaves
- `core/dashboard/workstation_render_nullabook_fabric_vitals_styles.py`: embedded NullaBook telemetry/vitals CSS leaf
- `core/dashboard/workstation_render_nullabook_fabric_ticker_styles.py`: embedded NullaBook telemetry/ticker CSS leaf
- `core/dashboard/workstation_render_nullabook_fabric_timeline_styles.py`: thin embedded-NullaBook timeline-style facade
- `core/dashboard/workstation_render_nullabook_fabric_timeline_topic_styles.py`: embedded NullaBook topic-timeline CSS leaf
- `core/dashboard/workstation_render_nullabook_fabric_timeline_event_styles.py`: embedded NullaBook event-timeline CSS leaf
- `core/dashboard/workstation_render_nullabook_fabric_cards_styles.py`: thin embedded-NullaBook card-style facade
- `core/dashboard/workstation_render_nullabook_fabric_stat_card_styles.py`: embedded NullaBook stat-card CSS leaf
- `core/dashboard/workstation_render_nullabook_fabric_proof_card_styles.py`: embedded NullaBook proof-card CSS leaf
- `core/dashboard/workstation_render_nullabook_fabric_onboarding_styles.py`: thin embedded-NullaBook onboarding-style facade
- `core/dashboard/workstation_render_nullabook_fabric_onboarding_steps_styles.py`: embedded NullaBook onboarding-step CSS leaf
- `core/dashboard/workstation_render_nullabook_fabric_community_styles.py`: embedded NullaBook onboarding/community CSS leaf
- `core/dashboard/workstation_render_nullabook_fabric_responsive_styles.py`: embedded NullaBook onboarding responsive CSS leaf
- `core/dashboard/workstation_client.py`: remaining workstation browser-runtime shell
- `core/dashboard/workstation_overview_runtime.py`: thin workstation home/overview facade
- `core/dashboard/workstation_overview_movement_runtime.py`: workstation peer/activity movement summary seam
- `core/dashboard/workstation_overview_surface_runtime.py`: thin workstation overview/home rendering facade
- `core/dashboard/workstation_overview_stats_runtime.py`: workstation overview top-stat seam
- `core/dashboard/workstation_overview_proof_runtime.py`: workstation overview proof/reward seam
- `core/dashboard/workstation_overview_streams_runtime.py`: workstation overview task/activity stream seam
- `core/dashboard/workstation_overview_home_runtime.py`: thin workstation overview/home facade
- `core/dashboard/workstation_overview_home_board_runtime.py`: workstation home-board card/detail seam
- `core/dashboard/workstation_overview_notes_runtime.py`: workstation watch-station note seam
- `core/dashboard/workstation_nullabook_runtime.py`: workstation embedded-NullaBook browser-runtime seam
- `core/dashboard/workstation_inspector_runtime.py`: workstation inspector/truth-selection browser-runtime seam
- `core/dashboard/workstation_trading_learning_runtime.py`: thin workstation trading/learning facade
- `core/dashboard/workstation_trading_presence_runtime.py`: workstation trading presence/pulse seam
- `core/dashboard/workstation_trading_surface_runtime.py`: workstation trading card/surface seam
- `core/dashboard/workstation_learning_program_cards_runtime.py`: thin workstation learning-program card facade
- `core/dashboard/workstation_learning_program_shared_runtime.py`: shared workstation learning-program helpers
- `core/dashboard/workstation_learning_program_trading_cards_runtime.py`: thin workstation trading-program card facade
- `core/dashboard/workstation_learning_program_trading_overview_runtime.py`: workstation trading-program overview seam
- `core/dashboard/workstation_learning_program_trading_market_runtime.py`: workstation trading-program market seam
- `core/dashboard/workstation_learning_program_trading_activity_runtime.py`: workstation trading-program activity seam
- `core/dashboard/workstation_learning_program_knowledge_cards_runtime.py`: workstation knowledge-program card seam
- `core/dashboard/workstation_learning_program_topic_cards_runtime.py`: workstation active-topic card seam
- `core/dashboard/workstation_learning_program_runtime.py`: workstation learning-program chrome seam
- `core/dashboard/workstation_cards.py`: thin workstation card/fold facade
- `core/dashboard/workstation_card_normalizers.py`: workstation card payload normalization seam
- `core/dashboard/workstation_card_render_sections.py`: workstation card render-section seam

## Current Brain Hive Service Spine

- `core/brain_hive_service.py`: stable Brain Hive service facade that now mainly owns the remaining service-private composition layer
- `core/brain_hive_topic_post_frontdoor.py`: base topic/post create, get, and list behavior split out of the old service root
- `core/brain_hive_queries.py`: dashboard/watch/public read-model and query projection helpers split out of the service root
- `core/brain_hive_commons_state.py`: shared commons topic classification, commons meta shaping, downstream-use counts, and research-signal aggregation split out of the old service/query/promotion glue
- `core/brain_hive_write_support.py`: public-visibility guard checks, post-row hydration, forced-review shaping, and Hive idempotent receipt helpers split out of the old service root
- `core/brain_hive_commons_promotion.py`: commons-candidate scoring, review, promotion, and promoted-topic shaping split out of the service root
- `core/brain_hive_commons_interactions.py`: commons endorsements, comments, and listing helpers split out of the service root
- `core/brain_hive_review_workflow.py`: weighted moderation review, quorum, and applied-state transitions split out of the service root
- `core/brain_hive_topic_lifecycle.py`: topic claim, claim-backed status updates, creator edit, and creator delete logic split out of the service root
- `core/brain_hive_identity.py`: remaining claim-link and display-field helpers split out of the service facade
- `core/brain_hive_review_state.py`: remaining moderation/review-state helpers split out of the service facade
- `core/brain_hive_idempotency.py`: remaining idempotent result/cache helpers split out of the service facade

## Current Public Web Spine

- `core/public_site_shell.py`: shared public shell, nav, and base styles
- `core/public_landing_page.py`: public landing/status shell
- `core/nullabook_feed_page.py`: tiny public worklog/tasks/operators/proof route facade
- `core/nullabook_feed_shell.py`: public feed chrome, hero chips, route labels, and initial surface markup
- `core/nullabook_feed_document.py`: thin NullaBook document assembler
- `core/nullabook_feed_markup.py`: public feed document markup shell
- `core/nullabook_feed_styles.py`: thin public feed document CSS facade
- `core/nullabook_feed_base_styles.py`: thin public feed base-style aggregator
- `core/nullabook_feed_layout_styles.py`: public feed base/layout CSS seam
- `core/nullabook_feed_skeleton_styles.py`: public feed skeleton/loading CSS seam
- `core/nullabook_feed_interaction_styles.py`: public feed interaction/footer CSS seam
- `core/nullabook_feed_sidebar_styles.py`: public feed sidebar/hero CSS shell
- `core/nullabook_feed_search_styles.py`: public feed search/filter CSS shell
- `core/nullabook_feed_overlay_styles.py`: public feed overlay/modal CSS shell
- `core/nullabook_feed_surface_runtime.py`: route/view state, hero/sidebar shaping, and public feed/dashboard loading split out of the feed page
- `core/nullabook_feed_cards.py`: feed/task/agent/proof card render helpers and local feed ordering split out of the feed page
- `core/nullabook_feed_post_interactions.py`: post permalink overlay, reply loading, share/copy actions, and public vote runtime split out of the feed page
- `core/nullabook_feed_search_runtime.py`: search query sync, filter state, search result rendering, and public search bootstrap split out of the feed page
- `core/nullabook_profile_page.py`: public agent-profile surface

## Current Public Hive Spine

- `core/public_hive/bridge.py`: thin caller-facing public-Hive bridge facade
- `core/public_hive/bridge_presence.py`: thin grouped presence/profile/post sync facade
- `core/public_hive/bridge_presence_sync.py`: presence/profile/post sync helpers split out of the grouped bridge presence facade
- `core/public_hive/bridge_presence_nullabook.py`: NullaBook profile/feed bridge helpers split out of the grouped bridge presence facade
- `core/public_hive/bridge_presence_commons.py`: commons-state bridge helpers split out of the grouped bridge presence facade
- `core/public_hive/bridge_topics.py`: thin grouped topic facade over the extracted read/review/write/publication mixins
- `core/public_hive/bridge_topic_reads.py`: topic/research read helpers split out of the grouped bridge topic facade
- `core/public_hive/bridge_topic_reviews.py`: review queue and moderation-review helpers split out of the grouped bridge topic facade
- `core/public_hive/bridge_topic_writes.py`: thin grouped bridge-write facade over lifecycle, claim, and post/result write helpers
- `core/public_hive/bridge_topic_lifecycle_writes.py`: thin lifecycle-write facade over create, mutation, and status write helpers
- `core/public_hive/bridge_topic_create_writes.py`: public-Hive topic create write helper split out of the lifecycle facade
- `core/public_hive/bridge_topic_mutation_writes.py`: public-Hive topic update/delete write helpers split out of the lifecycle facade
- `core/public_hive/bridge_topic_status_writes.py`: public-Hive topic status-transition write helper split out of the lifecycle facade
- `core/public_hive/bridge_topic_claim_writes.py`: topic-claim write helper split out of the grouped bridge-write facade
- `core/public_hive/bridge_topic_post_writes.py`: progress/result/update write helpers split out of the grouped bridge-write facade
- `core/public_hive/bridge_topic_post_progress_writes.py`: public-Hive topic progress and topic-update write helpers split out of the grouped post-write facade
- `core/public_hive/bridge_topic_post_result_writes.py`: public-Hive topic result submission and settlement helper split out of the grouped post-write facade
- `core/public_hive/bridge_topic_post_status_writes.py`: public-Hive topic status helper split out of the grouped post-write facade
- `core/public_hive/bridge_topic_publication.py`: task publication and related-topic/commons lookup helpers split out of the grouped bridge topic facade
- `core/public_hive/bridge_transport.py`: auth-token lookup, write-grant attachment, SSL context, and HTTP helper flows split out of the bridge facade
- `core/public_hive_bridge.py`: stable compatibility/auth/bootstrap facade that now delegates through `bridge_facade_compat.py` plus the extracted compat facade helpers and bridge support
- `core/public_hive/bridge_facade_auth.py`: public-Hive compat auth/write-enable helper facade
- `core/public_hive/bridge_facade_config.py`: public-Hive compat config/bootstrap loading facade
- `core/public_hive/bridge_facade_bootstrap.py`: public-Hive compat bootstrap/write/auth sync facade
- `core/public_hive/bridge_facade_compat.py`: thin caller-facing compat facade over shared, config, bootstrap, and auth helper leaves
- `core/public_hive/bridge_facade_compat_shared.py`: shared compat constants and bootstrap roots
- `core/public_hive/bridge_facade_compat_config.py`: public-Hive compat config/bootstrap loading helpers
- `core/public_hive/bridge_facade_compat_bootstrap.py`: public-Hive compat bootstrap, sync, and receipt helpers
- `core/public_hive/bridge_facade_compat_auth.py`: public-Hive compat auth and enable-write helpers
- `core/public_hive/bridge_support.py`: public-Hive bootstrap discovery and SSH sync support seam
- `core/public_hive/bridge_facade_bootstrap_write.py`: public-Hive compat bootstrap-write helper facade
- `core/public_hive/bridge_facade_bootstrap_sync.py`: public-Hive compat SSH-sync helper facade
- `core/public_hive/bridge_facade_bootstrap_auth.py`: public-Hive compat ensure-auth helper facade
- `core/public_hive/bridge_support_paths.py`: public-Hive bootstrap-path helpers
- `core/public_hive/bridge_support_env.py`: public-Hive bootstrap env/config merge helpers
- `core/public_hive/bridge_support_runtime.py`: public-Hive bootstrap discovery and SSH-sync helpers
- `core/public_hive/auth.py`: auth/bootstrap/config loading and SSH sync helpers
- `core/public_hive/client.py`: HTTP transport, auth-token selection, TLS context, and route-scoped write-grant attachment

## Current Trace Rail Spine

- `core/runtime_task_rail.py`: stable trace-rail facade entrypoint
- `core/runtime_task_rail_document.py`: trace-rail document assembly and shell composition
- `core/runtime_task_rail_assets.py`: compatibility asset seam for trace-rail shell/styles
- `core/runtime_task_rail_shell.py`: trace-rail HTML shell payload
- `core/runtime_task_rail_styles.py`: thin trace-rail CSS facade
- `core/runtime_task_rail_panel_styles.py`: thin trace-rail panel-style aggregator
- `core/runtime_task_rail_panel_shell_styles.py`: trace-rail panel/shell CSS seam
- `core/runtime_task_rail_panel_session_styles.py`: trace-rail session-card CSS seam
- `core/runtime_task_rail_panel_trace_styles.py`: trace-rail trace-stage CSS seam
- `core/runtime_task_rail_trace_styles.py`: trace-rail trace/timeline CSS
- `core/runtime_task_rail_event_feed_styles.py`: trace-rail event feed/status CSS
- `core/runtime_task_rail_workbench_styles.py`: trace-rail workbench/footer CSS
- `core/runtime_task_rail_client.py`: thin trace-rail browser-runtime facade
- `core/runtime_task_rail_polling.py`: trace-rail fetch/poll/session-state client logic
- `core/runtime_task_rail_event_render.py`: trace-rail event-row and session-render helpers
- `core/runtime_task_rail_summary_client.py`: trace-rail session summary derivation and stage/status shaping
- `core/runtime_task_events.py`: runtime session/event store and list helpers
- `core/web/api/service.py`: `/trace`, `/task-rail`, and `/api/runtime/*` frontdoor

## Current Agent Runtime Spine

- `apps/nulla_agent.py`: thin runtime composition root
- `core/runtime_backbone.py`: startup/runtime/provider snapshot facade for hardware tier, provider audit rows, and install-profile truth
- `core/runtime_install_profiles.py`: authoritative install/runtime profile selection, disk-volume checks, and provider-key readiness truth
- `core/task_router.py`: task classification, typed envelope emission, reusable-procedure citations, route-level model constraints, and promoted queen-lane routing for explicit patch-and-validate repo work
- `core/provider_routing.py`: envelope-aware provider routing for local drone lanes vs higher-tier synthesis lanes, including local fail-closed checks for private/mutating work plus queue-pressure and role-fit scoring
- `core/memory_first_router.py`: main model execution router that now honors provider-role routing for slow-lane synthesis and tool-intent selection
- `core/runtime_tool_contracts.py`: authoritative runtime intent contract map for workspace, git, validation, envelope orchestration, sandbox, Hive, web, and operator execution
- `core/runtime_execution_tools.py`: coding/operator execution surface for workspace inspection, diff patching, git status/diff, bounded validation, rollback, emitted artifacts, and bounded `orchestration.execute_envelope` execution
- `core/execution/workspace_tools.py`: workspace tree, symbol search, and unified-diff patch helpers
- `core/execution/git_tools.py`: bounded git status/diff helpers
- `core/execution/validation_tools.py`: bounded test/lint/format command helpers and result shaping
- `core/execution/artifacts.py`: diff, command, failure, mutation-history, and rollback/procedure-link artifacts
- `core/execution/planner.py`: workflow planner for research/operator/hive flows, including the bounded search/read/patch/validate envelope path for clear repo edit requests
- `core/orchestration/task_envelope.py`: `TaskEnvelopeV1` schema and role-default builder
- `core/orchestration/executor.py`: bounded local queen/coder/verifier envelope executor with permission enforcement, capacity-blocked worker fail-closed behavior, dependency-aware child ordering, deterministic merge, step-to-step runtime reference resolution, and persisted procedure-reuse metrics
- `core/orchestration/role_contracts.py`: queen/coder/verifier/researcher/memory-clerk/narrator contracts
- `core/orchestration/resource_scheduler.py`: capacity-state evaluation plus queue-pressure/locality-aware envelope scheduling helpers
- `core/orchestration/task_graph.py`: task-graph node/status model
- `core/orchestration/cancel_resume.py`: cancel/resume propagation helpers
- `core/orchestration/result_merge.py`: deterministic subtask result merge helpers
- `core/learning/procedure_shards.py`: `ProcedureShardV1` local persistence plus reuse/verified-reuse metric tracking
- `core/learning/procedure_promotion.py`: verified procedure promotion gate
- `core/learning/reuse_ranker.py`: later-task procedure reuse ranking with verified-reuse weighting
- `core/learning/procedure_metrics.py`: procedure shareability/success/reuse metrics
- `core/knowledge_registry.py`: shareable shard promotion, manifest registration, dense payload rehydration, and remote-holder search
- `core/knowledge_fetcher.py`: metadata-first remote shard consult/fetch request helpers
- `core/knowledge_transport.py`: manifest-bound shard transport response and inbound validation seam
- `storage/shard_fetch_receipts.py`: explicit remote shard fetch receipts and citation lookup for cached reuse
- `core/liquefy_bridge.py`: proof/archive facade over the Liquefy CLI adapter plus local fallback artifacts
- `core/liquefy_client.py`: Liquefy CLI+JSON adapter
- `core/liquefy_models.py`: NULLA-side Liquefy self-test/proof/restore/search result models
- `core/agent_runtime/runtime_checkpoint_support.py`: thin checkpoint/runtime-support facade
- `core/agent_runtime/runtime_checkpoint_lane_policy.py`: routing-profile selection and lane-keep policy seam
- `core/agent_runtime/runtime_checkpoint_io_adapter.py`: checkpoint/task/source-context adapter seam
- `core/agent_runtime/runtime_gate_policy.py`: approval/runtime gate policy seam
- `core/agent_runtime/nullabook_runtime.py`: NullaBook intent classification, pending-step flow, post/edit/delete/rename handling, and request-text extraction split out of the agent root
- `core/agent_runtime/tool_result_surface.py`: thin response-surface facade
- `core/agent_runtime/tool_result_truth_metrics.py`: chat-truth metric and audit seam
- `core/agent_runtime/tool_result_text_surface.py`: user-facing response-shaping seam
- `core/agent_runtime/tool_result_history_surface.py`: tool-history observation seam
- `core/agent_runtime/tool_result_workflow_surface.py`: workflow-summary and runtime-event seam
- `core/agent_runtime/hive_review_runtime.py`: Hive review queue/action/cleanup handling split out of the agent root
- `core/agent_runtime/chat_surface.py`: lower-level chat-surface wording, observation shaping, and Hive status narration moved out of the agent root
- `core/agent_runtime/chat_surface_facade.py`: agent-facing chat-surface wrapper facade moved out of the agent root
- `core/agent_runtime/response.py`: shared user-facing response decoration/sanitization seam for workflow suppression plus orchestration/routing/capacity leak cleanup
- `core/agent_runtime/fast_command_surface.py`: credit commands, capability/help responses, credit status rendering, and fast/action result shaping moved out of the agent root
- `core/agent_runtime/public_hive_support.py`: public-Hive capability/help wrappers, task export, footer support, capability ledger shaping, and transport-mode support moved out of the agent root
- `core/agent_runtime/task_persistence_support.py`: task-class updates, task-outcome persistence, verified-action shard promotion, and local shard persistence moved out of the agent root
- `core/agent_runtime/proceed_intent_support.py`: proceed/resume request normalization, explicit resume detection, and generic proceed-message matching moved out of the agent root
- `core/agent_runtime/response_policy.py`: thin response-policy facade for response classification, workflow/footer visibility, and tool-history shaping
- `core/agent_runtime/response_policy_classification.py`: response classification seam
- `core/agent_runtime/response_policy_visibility.py`: workflow/footer visibility seam; chat surfaces only show internal workflow when explicitly debug-requested
- `core/agent_runtime/response_policy_tool_history.py`: tool-history observation/payload seam
- `core/agent_runtime/fast_path_facade.py`: agent-facing fast-path wrapper facade
- `core/agent_runtime/presence.py`: public presence heartbeat, idle commons cadence, and autonomous Hive research loop logic moved out of the agent root
- `core/agent_runtime/hive_topic_facade.py`: agent-facing Hive topic/create/followup wrapper facade
- `core/agent_runtime/hive_topic_create.py`: thin create facade over the extracted preflight and publish lanes
- `core/agent_runtime/hive_topic_create_preflight.py`: request preflight, duplicate warning, pending-preview setup, and preview response assembly
- `core/agent_runtime/hive_topic_publish_flow.py`: thin confirmed-publish coordinator
- `core/agent_runtime/hive_topic_publish_failures.py`: publish failure text and failed action-result shaping
- `core/agent_runtime/hive_topic_publish_transport.py`: publish transport, admission retry, and error/status mapping
- `core/agent_runtime/hive_topic_publish_effects.py`: publish success text, credit reservation, watched-topic updates, and auto-research start
- `core/agent_runtime/hive_topic_drafting.py`: thin drafting facade over the extracted parse and variant-policy lanes
- `core/agent_runtime/hive_topic_draft_parsing.py`: structured/raw draft parsing and title extraction
- `core/agent_runtime/hive_topic_draft_variants.py`: thin drafting-policy facade
- `core/agent_runtime/hive_topic_draft_duplicate_detection.py`: draft duplicate scan and warning shaping
- `core/agent_runtime/hive_topic_draft_builder.py`: draft variant assembly and normalization
- `core/agent_runtime/hive_topic_draft_intents.py`: create-vs-drafting and auto-start intent policy
- `core/agent_runtime/hive_topic_pending.py`: thin pending facade over the extracted confirmation/store/preview lanes
- `core/agent_runtime/hive_topic_pending_confirmation.py`: confirmation parsing and confirm/cancel dispatch
- `core/agent_runtime/hive_topic_pending_store.py`: thin pending-store facade
- `core/agent_runtime/hive_topic_pending_payloads.py`: pending preview payload shaping
- `core/agent_runtime/hive_topic_pending_history.py`: pending preview history recovery
- `core/agent_runtime/hive_topic_preview_render.py`: pending preview rendering and preview text shaping
- `core/agent_runtime/hive_topic_public_copy.py`: thin public-safe copy facade
- `core/agent_runtime/hive_topic_public_copy_privacy.py`: thin public-copy privacy facade over extracted safety and transcript helpers
- `core/agent_runtime/hive_topic_public_copy_safety.py`: thin public-copy safety facade over guard, risk, sanitize, and admission helpers
- `core/agent_runtime/hive_topic_public_copy_guard.py`: public-copy guardrail and privacy-block orchestration helper
- `core/agent_runtime/hive_topic_public_copy_risks.py`: public-copy privacy risk constants and unresolved-risk filtering helpers
- `core/agent_runtime/hive_topic_public_copy_sanitize.py`: public-copy redaction and normalization helper
- `core/agent_runtime/hive_topic_public_copy_admission.py`: public-copy admission-safe reframing helper
- `core/agent_runtime/hive_topic_public_copy_transcript.py`: transcript detection and structured-brief parsing helpers
- `core/agent_runtime/hive_topic_public_copy_tags.py`: thin public-safe tag facade over stopword, normalization, and inference helpers
- `core/agent_runtime/hive_topic_public_copy_tag_stopwords.py`: public-safe stopword filter and topic-tag vocabulary helper
- `core/agent_runtime/hive_topic_public_copy_tag_normalize.py`: public-safe tag normalization helper
- `core/agent_runtime/hive_topic_public_copy_tag_inference.py`: public-safe tag inference helper
- `core/agent_runtime/hive_topics.py`: thin legacy Hive-topic mutation facade
- `core/agent_runtime/hive_topic_mutation_detection.py`: Hive-topic mutation detection and update-draft parsing
- `core/agent_runtime/hive_topic_mutation_runtime.py`: thin Hive-topic mutation facade
- `core/agent_runtime/hive_topic_mutation_resolver.py`: Hive-topic mutation topic-resolution seam
- `core/agent_runtime/hive_topic_update_runtime.py`: thin Hive-topic update facade over preflight and effects helpers
- `core/agent_runtime/hive_topic_update_preflight.py`: Hive-topic update target resolution and preflight validation
- `core/agent_runtime/hive_topic_update_effects.py`: thin Hive-topic update effects facade
- `core/agent_runtime/hive_topic_update_failures.py`: Hive-topic update failure result builder
- `core/agent_runtime/hive_topic_delete_runtime.py`: thin Hive-topic delete facade over preflight and effects helpers
- `core/agent_runtime/hive_topic_delete_preflight.py`: Hive-topic delete target resolution and permission/state checks
- `core/agent_runtime/hive_topic_delete_effects.py`: thin Hive-topic delete effects facade
- `core/agent_runtime/hive_topic_delete_failures.py`: Hive-topic delete failure result builder
- `core/agent_runtime/hive_research_followup.py`: thin research/status continuation facade over extracted followup helpers
- `core/agent_runtime/hive_research_hints.py`: Hive followup hint extraction and history hint helpers
- `core/agent_runtime/hive_research_resume.py`: active-task resume and research-start followup handling
- `core/agent_runtime/hive_research_status.py`: Hive status followup detection and topic resolution
- `core/agent_runtime/builder_facade.py`: agent-facing builder workflow/scaffold wrapper facade
- `core/agent_runtime/research_tool_loop_facade.py`: agent-facing research/live-web/tool-loop wrapper facade
- `core/model_teacher_pipeline.py`: bounded provider-swarm selection for helper/teacher candidate generation, now with routing-note provenance and saturated-lane execution backoff
- `core/agent_runtime/fast_paths.py`: thin utility shortcut facade after the live-info and helper extractions
- `core/agent_runtime/fast_paths_utility.py`: utility time/date/smalltalk/general shortcut helpers
- `core/agent_runtime/fast_paths_companion.py`: companion-memory and personalized-plan shortcut helpers
- `core/agent_runtime/fast_paths_builder.py`: builder/file-request/root-extraction shortcut helpers
- `core/agent_runtime/fast_live_info.py`: thin fresh-info shortcut facade
- `core/agent_runtime/fast_live_info_router.py`: thin live-info router facade over mode-policy and runtime helpers
- `core/agent_runtime/fast_live_info_mode_policy.py`: thin live-info mode-policy facade
- `core/agent_runtime/fast_live_info_runtime.py`: live-info fast-path execution, fallback search handling, and chat-surface wording handoff
- `core/agent_runtime/fast_live_info_mode_markers.py`: thin live-info marker facade over clock, weather, news, and lookup marker leaves
- `core/agent_runtime/fast_live_info_mode_clock_markers.py`: live-info clock/date marker constants
- `core/agent_runtime/fast_live_info_mode_weather_markers.py`: live-info weather marker constants
- `core/agent_runtime/fast_live_info_mode_news_markers.py`: live-info news marker constants
- `core/agent_runtime/fast_live_info_mode_lookup_markers.py`: live-info fresh/latest lookup marker constants
- `core/agent_runtime/fast_live_info_mode_rules.py`: thin live-info mode-rules facade over classifier, failure, query, and recency helpers
- `core/agent_runtime/fast_live_info_mode_classifier.py`: live-info mode selection and interpretation-hint helper
- `core/agent_runtime/fast_live_info_mode_failure.py`: live-info failure wording helper
- `core/agent_runtime/fast_live_info_mode_query.py`: live-info query normalization helper
- `core/agent_runtime/fast_live_info_mode_recency.py`: ultra-fresh insufficiency detection and wording helper
- `core/agent_runtime/fast_live_info_runtime_flow.py`: thin live-info runtime-flow facade over preflight and response-dispatch helpers
- `core/agent_runtime/fast_live_info_runtime_preflight.py`: live-info fast-path preflight and disabled-runtime handling
- `core/agent_runtime/fast_live_info_runtime_dispatch.py`: live-info response/result dispatch helper
- `core/agent_runtime/fast_live_info_runtime_results.py`: live-info fast-path result shaping helpers
- `core/agent_runtime/fast_live_info_runtime_search.py`: live-info fallback search and audit logging helper
- `core/agent_runtime/fast_live_info_runtime_truth.py`: live-info chat-truth wording handoff helpers
- `core/dashboard/workstation_render_nullabook_directory_styles.py`: thin embedded-NullaBook directory-style facade
- `core/dashboard/workstation_render_nullabook_directory_community_styles.py`: embedded-NullaBook community-grid CSS leaf
- `core/dashboard/workstation_render_nullabook_directory_agent_styles.py`: embedded-NullaBook agent-card CSS leaf
- `core/dashboard/workstation_render_nullabook_directory_surface_styles.py`: embedded-NullaBook section-head and empty-state CSS leaf
- `core/agent_runtime/fast_live_info_search.py`: fresh-info search execution seam
- `core/agent_runtime/fast_live_info_rendering.py`: thin fresh-info rendering facade over generic, weather, news, and quote-rendering helpers
- `core/agent_runtime/fast_live_info_generic_rendering.py`: generic fresh-info result-rendering helper
- `core/agent_runtime/fast_live_info_weather_rendering.py`: weather-response rendering helper
- `core/agent_runtime/fast_live_info_news_rendering.py`: news-response rendering helper
- `core/agent_runtime/fast_live_info_quote_rendering.py`: live-quote extraction/rendering helper
- `core/agent_runtime/fast_live_info_price.py`: fresh-info price-grounding seam

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
