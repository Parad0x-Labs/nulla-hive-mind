# NULLA LLM Acceptance Summary

Tracked snapshot only. This file is the last committed live LLM acceptance report, not an automatically current-head report. Check [`docs/STATUS.md`](./STATUS.md) and [`docs/LOCAL_ACCEPTANCE.md`](./LOCAL_ACCEPTANCE.md) before treating it as current branch truth.

- snapshot commit SHA: dcce6606f49ad938bafe75a0b26b8628af07e2c9
- branch: codex/honest-ollama-prewarm-bootstrap
- test run timestamp: 2026-03-28T20:15:19Z
- environment: macOS-26.3-arm64-arm-64bit | python 3.11.15 | cpu Apple M4 | ram 24.0 GB | gpu Apple M4
- model/runtime configuration: {'profile_id': 'local-qwen25-7b-v1', 'profile_name': 'NULLA local acceptance for qwen2.5:7b', 'model': 'qwen2.5:7b', 'base_url': 'http://127.0.0.1:18080'}
- recent baseline comparison: unchanged
- overall full gate: GREEN
- ci fast gate: GREEN
- preserved previous non-green output bundle: reports/llm_eval/latest_preserved_blocked_20260328T201519Z
- preserved previous live acceptance bundle: none

## Pass / Fail Summary

- recent 48h regression: pass
- live runtime acceptance: pass
- context discipline: pass
- research quality: pass
- hive integrity: pass
- nullabook provenance: pass

## Latency Findings

- overall p50: 0.463
- overall p95: 38.519
- overall p99: 82.754
- overall max: 93.813

| Request Type | Samples | p50 | p95 | p99 | max |
| --- | ---: | ---: | ---: | ---: | ---: |
| chained_task | 1 | 0.687 | 0.687 | 0.687 | 0.687 |
| cold_start | 1 | 93.813 | 93.813 | 93.813 | 93.813 |
| consistency_repeat | 3 | 0.379 | 0.498 | 0.508 | 0.511 |
| freshness_honesty | 1 | 0.024 | 0.024 | 0.024 | 0.024 |
| instruction_fidelity | 1 | 0.673 | 0.673 | 0.673 | 0.673 |
| offline_honesty | 1 | 0.03 | 0.03 | 0.03 | 0.03 |
| recovery | 1 | 1.176 | 1.176 | 1.176 | 1.176 |
| research_lookup | 1 | 0.293 | 0.293 | 0.293 | 0.293 |
| tool_invocation | 3 | 0.463 | 3.321 | 3.575 | 3.639 |
| warm_logic | 1 | 0.029 | 0.029 | 0.029 | 0.029 |
| warm_simple | 1 | 14.822 | 14.822 | 14.822 | 14.822 |

## Context Discipline Findings

| Scenario | Status | Duration (s) | Target |
| --- | --- | ---: | --- |
| active_task_followup_short_id | pass | 1.081 | `tests/test_openclaw_tooling_context.py::OpenClawToolingContextTests::test_openclaw_can_confirm_short_followup_after_hive_task_list` |
| fresh_short_id_reference | pass | 0.407 | `tests/test_openclaw_tooling_context.py::OpenClawToolingContextTests::test_openclaw_can_start_hive_task_from_fresh_short_id_reference` |
| history_recovery_followup | pass | 0.41 | `tests/test_openclaw_tooling_context.py::OpenClawToolingContextTests::test_openclaw_can_confirm_short_followup_from_recent_history_when_session_state_is_empty` |
| stale_active_task_not_sticky | pass | 0.514 | `tests/test_openclaw_tooling_context.py::OpenClawToolingContextTests::test_openclaw_hive_create_confirm_beats_stale_active_task_state` |
| watched_topic_followup | pass | 0.444 | `tests/test_openclaw_tooling_context.py::OpenClawToolingContextTests::test_openclaw_hive_status_followup_uses_watched_topic_context` |
| recent_history_topic_followup | pass | 0.436 | `tests/test_openclaw_tooling_context.py::OpenClawToolingContextTests::test_openclaw_hive_status_followup_can_resolve_topic_from_recent_history` |
| vilnius_short_followup | pass | 0.443 | `tests/test_nulla_runtime_contracts.py::test_short_vilnius_time_followup_reuses_recent_time_context` |
| vilnius_malformed_followup | pass | 0.481 | `tests/test_nulla_runtime_contracts.py::test_exact_vilnius_malformed_followup_reuses_session_time_context` |
| stale_person_context_purged_for_math | pass | 0.484 | `tests/test_nulla_runtime_contracts.py::test_direct_math_overrides_stale_toly_context` |
| hive_problem_review_followup | pass | 0.446 | `tests/test_nulla_hive_task_flow.py::test_review_the_problem_clarifies_when_multiple_tasks_are_open` |

## Research Quality Findings

| Scenario | Status | Duration (s) | Target |
| --- | --- | ---: | --- |
| planned_search_for_live_updates | pass | 0.419 | `tests/test_nulla_web_freshness_and_lookup.py::test_latest_telegram_updates_trigger_planned_web_lookup` |
| offline_honesty | pass | 0.414 | `tests/test_nulla_web_freshness_and_lookup.py::test_live_info_without_web_fallback_returns_deterministic_disabled_response` |
| ultra_fresh_honesty | pass | 0.414 | `tests/test_nulla_web_freshness_and_lookup.py::test_ultra_fresh_market_question_returns_insufficient_evidence_without_bluffing` |
| structured_weather_lookup | pass | 0.427 | `tests/test_nulla_web_freshness_and_lookup.py::test_weather_live_lookup_uses_structured_weather_wording` |
| structured_news_lookup | pass | 0.421 | `tests/test_nulla_web_freshness_and_lookup.py::test_news_live_lookup_uses_structured_headline_wording` |
| weak_evidence_uncertainty | pass | 0.434 | `tests/test_nulla_web_freshness_and_lookup.py::test_adaptive_research_surfaces_uncertainty_when_evidence_stays_weak` |
| empty_lookup_honesty | pass | 0.425 | `tests/test_nulla_web_freshness_and_lookup.py::test_empty_fresh_lookup_honestly_degrades_instead_of_using_memory_as_final_speaker` |
| openclaw_live_lookup | pass | 0.442 | `tests/test_openclaw_tooling_context.py::OpenClawToolingContextTests::test_openclaw_surface_triggers_live_web_lookup_for_fresh_requests` |

## Hive Integrity Findings

| Scenario | Status | Duration (s) | Target |
| --- | --- | ---: | --- |
| ux_preview_before_confirm | pass | 0.474 | `tests/test_openclaw_tooling_context.py::OpenClawToolingContextTests::test_openclaw_hive_task_preview_beats_twitter_route_and_stays_clean` |
| confirm_posts_improved_copy | pass | 0.501 | `tests/test_openclaw_tooling_context.py::OpenClawToolingContextTests::test_openclaw_hive_create_yes_improved_posts_improved_copy` |
| unsigned_write_blocked | pass | 1.012 | `tests/test_meet_and_greet_service.py::MeetAndGreetServerDispatchTests::test_http_server_requires_signed_write_envelope` |
| spoofed_update_blocked | pass | 0.936 | `tests/test_meet_and_greet_service.py::MeetAndGreetServerDispatchTests::test_http_server_rejects_spoofed_topic_update_actor` |
| status_validation_no_mutation | pass | 1.0 | `tests/test_meet_and_greet_service.py::MeetAndGreetServerDispatchTests::test_http_server_failed_status_validation_does_not_mutate_topic` |
| reward_release_once | pass | 0.413 | `tests/test_reward_engine.py::RewardEngineTests::test_releasing_mature_reward_mints_compute_credits_once` |
| reward_finalization_ordered | pass | 0.462 | `tests/test_reward_engine.py::RewardEngineTests::test_confirmed_reward_finalizes_after_quiet_window` |
| late_negative_review_blocks_finality | pass | 0.44 | `tests/test_reward_engine.py::RewardEngineTests::test_negative_review_after_confirmation_slashes_work` |

## NullaBook Provenance Findings

| Scenario | Status | Duration (s) | Target |
| --- | --- | ---: | --- |
| token_identity_mismatch_blocked | pass | 0.983 | `tests/test_meet_and_greet_service.py::MeetAndGreetServerDispatchTests::test_http_server_rejects_nullabook_post_token_identity_mismatch` |
| auth_channel_sets_origin | pass | 0.967 | `tests/test_meet_and_greet_service.py::MeetAndGreetServerDispatchTests::test_http_server_sets_nullabook_post_provenance_from_auth_channel` |
| runtime_fast_path_marks_ai_origin | pass | 0.428 | `tests/test_agent_runtime_nullabook.py::test_execute_nullabook_post_marks_runtime_posts_as_ai_origin` |
| api_ignores_client_provenance_spoof | pass | 0.429 | `tests/test_nullabook_api.py::test_create_post_ignores_client_supplied_provenance_fields` |
| store_default_human_origin | pass | 0.427 | `tests/test_nullabook_store.py::test_create_post` |
| store_explicit_ai_origin | pass | 0.425 | `tests/test_nullabook_store.py::test_create_post_supports_explicit_ai_provenance` |

## Regressions

- 48h pack comparison: unchanged
- baseline path: reports/llm_eval/baselines/recent_48h_regression.json

## Blockers

- none

## Exact Failing Tests

- none

## Next Actions

- Keep the 48h regression baseline current only from real passing runs.
- Treat provenance or reward integrity regressions as hard release blockers.
- Latest live acceptance evidence: `artifacts/acceptance_runs/llm_eval_live/evidence/NULLA_LOCAL_ACCEPTANCE_REPORT.md`.
- Re-run the live lane whenever the runtime model, tool path, or acceptance thresholds change.
