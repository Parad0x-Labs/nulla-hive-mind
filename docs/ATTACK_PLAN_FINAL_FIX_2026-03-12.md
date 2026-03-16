# NULLA Final Attack Plan — Capability Closure

**Date:** 2026-03-12  
**Status:** Corrected final plan after repo verification and green baseline commit

## Brutal Verdict

The runtime-fix findings are directionally correct.

The old attack plan is not final enough for the actual goal.

It over-focuses on polish while the repo still has explicit future-spec gaps in:

- end-to-end builder autonomy,
- shard reuse and remote merge,
- companion inference and personalization,
- hive-mind delegation,
- and chat-level economics/world-computer behavior.

If the target is "acts as much like GPT/Claude as possible, local-first, real, useful, and able to carry work end to end", then formatting and greeting variants are not the center of gravity.

## Verified Current Baseline

- Runtime fixes from `CODEX_NULLA_REAL_TEST_FINDINGS_2026-03-12.md` are real.
- Current full suite on this machine: `535 passed, 8 skipped, 9 xfailed, 1 warning`.
- Current deep contract runner: `110 passed, 9 xfailed, 1 warning`.
- The remaining `xfail` tests are not regressions. They are the explicit frontier-gap contract.

## Do Not Misread The Situation

The remaining work is **not** "mostly quality".

It is:

- partly quality,
- but mainly capability closure.

The real missing product behavior is concentrated in the `xfail` set:

1. full `research -> code -> run -> verify` builder mode,
2. multi-helper hive-mind delegation and merge,
3. chat-visible earned/spent world-computer budget,
4. companion inference from sparse context,
5. stronger personalization from behavior,
6. shard-first answer synthesis,
7. remote shard fetch + merge into final answer.

## Final Execution Order

### Phase 0: Hold The Green Baseline

Goal:

- do not lose the now-green repo baseline while chasing bigger behavior.

Rules:

- every feature tranche must keep `pytest -q` green,
- every feature tranche must keep `sh scripts/run_nulla_deep_contract_tests.sh` green,
- no cosmetic work before capability gates are chosen.

Worktree:

- main repo on `codex/local-bootstrap` stays stable,
- active implementation should happen in `.worktrees/capability-lab` or `.worktrees/runtime-fixes`.

### Phase 1: Builder Mode End To End — NOW

Target test:

- `tests/test_nulla_future_vision_spec.py::test_future_builder_mode_can_research_generate_write_and_verify_without_manual_stitching`

What must become real:

- user asks for a build,
- agent performs source-planned research,
- agent writes a bounded scaffold or project artifact,
- agent runs a bounded verification step,
- final answer reports what was built, what ran, what failed, and which sources were used.

Required code areas:

- `apps/nulla_agent.py`
- `core/runtime_execution_tools.py`
- `core/tool_intent_executor.py`
- `retrieval/web_adapter.py`
- build-scaffold paths already present in `apps/nulla_agent.py`

Definition of done:

- test passes without `xfail`,
- answer includes verification result and source grounding,
- no fake "I built it" text when nothing executed.

### Phase 2: Shard Reuse And Remote Merge — NOW

Target tests:

- `tests/test_nulla_shards_and_reuse_spec.py::test_future_chat_reuses_best_shareable_shard_before_generic_model_fallback`
- `tests/test_nulla_shards_and_reuse_spec.py::test_future_remote_shard_fetch_merges_with_local_context_for_final_answer`

Why this is early:

- without real reuse, "learning", "hive mind", and "local-first memory" are mostly theater.

What must become real:

- chat path can actually load shard payloads, not just metadata,
- local shard payloads can shape the final answer,
- remote shard fetch can merge with local context in user-visible synthesis.

Required code areas:

- `core/knowledge_fetcher.py`
- `core/knowledge_registry.py`
- `retrieval/swarm_query.py`
- `apps/nulla_agent.py`

Definition of done:

- the answer changes because shard content was retrieved and used,
- the runtime can cite that reuse in a user-safe way,
- tests pass without `xfail`.

### Phase 3: Companion Inference And Personalization — NOW

Target tests:

- `tests/test_nulla_future_vision_spec.py::test_future_companion_mode_can_infer_user_needs_from_sparse_context`
- `tests/test_nulla_local_first_memory_and_personalization.py::test_future_personalization_can_infer_user_style_from_behavior_without_direct_commands`

Why this is early:

- if the system cannot continue work from thin context or adapt from durable behavior, it is not a real assistant. It is a session bot with memory garnish.

What must become real:

- heuristics and summaries must alter reply planning,
- session continuity must influence likely-next-task inference,
- user style and recurring project lanes must be reused without explicit commands every time.

Required code areas:

- `core/persistent_memory.py`
- `core/tiered_context_loader.py`
- `core/human_input_adapter.py`
- `apps/nulla_agent.py`

Definition of done:

- sparse prompts can recover likely project continuation,
- behavior-derived preferences actually shift response style or next-step selection,
- tests pass without `xfail`.

### Phase 4: Hive-Mind Delegation — NOW / NEXT

Target test:

- `tests/test_nulla_future_vision_spec.py::test_future_hive_mind_mode_can_delegate_to_multiple_helpers_and_merge_results`

Why this is after builder/shards/companion:

- delegation without usable synthesis becomes log spam.

What must become real:

- chat-level request can spawn multiple helper lanes,
- helper outputs are merged into one grounded user reply,
- the user can see that helper lanes were active without being buried in runtime noise.

Required code areas:

- `core/parent_orchestrator.py`
- `core/helper_scheduler.py`
- `core/task_reassembler.py`
- `apps/nulla_agent.py`

Definition of done:

- merged result is visible and useful,
- helper count and merge outcome are real, not narrated fiction,
- test passes without `xfail`.

### Phase 5: World Computer / Credits — LATER

Target tests:

- `tests/test_nulla_credits_and_hive_economy_spec.py::test_future_chat_can_spend_credits_to_prioritize_hive_task`
- `tests/test_nulla_credits_and_hive_economy_spec.py::test_future_chat_can_transfer_credits_to_another_peer`
- `tests/test_nulla_future_vision_spec.py::test_future_world_computer_mode_can_show_real_earned_and_spent_contribution_budget`

Why later:

- product usefulness does not depend on this yet,
- honesty does. Keep it later until execution, reuse, and delegation are real.

Definition of done:

- chat contract is real,
- balances, spending, and history are user-visible and truthful,
- tests pass without `xfail`.

## Demoted Work

These are still worth doing, but they are not the main line:

- greeting variants,
- Hive list prettification,
- softer wording on Telegram current-info answers,
- weather/news snippet cleanup.

Do them only after one real capability tranche lands.

## Worktree Mapping

- `.worktrees/runtime-fixes`
  - runtime regressions, adapter bugs, pathing, routing, sandbox/execution faults
- `.worktrees/capability-lab`
  - builder mode, shard reuse, companion inference, hive-mind merge
- `.worktrees/release-prep`
  - packaging, docs cleanup, repo hygiene, release polish after capability work

## Immediate Next Attack

1. Remove `xfail` from builder mode only in `capability-lab`.
2. Make that test pass honestly.
3. Keep full suite green.
4. Then attack shard reuse.
5. Then attack companion inference.

That is the shortest path toward something that feels materially more like a real assistant instead of a well-instrumented bounded prototype.
