# NULLA AI-First Requirements Checklist

Date: 2026-03-13

Source of truth:
- `docs/NULLA_AI_FIRST_REPO_AUDIT_2026-03-13.md`
- `docs/AI_FIRST_RUNTIME_PRODUCTION_PLAN_2026-03-13.md`

Rule:
- This checklist is the execution contract.
- Milestone 1 means P0 only. Nothing outside Milestone 1 should be implemented during the first patch round.

## Milestone 1: P0 Must-Fix Conversation Path

| ID | Requirement | Primary files | Ship check |
| --- | --- | --- | --- |
| M1-R01 | Add runtime instrumentation for chat-path truth: model final-answer hit rate, fast-path hit rate, planner leakage rate, template-renderer hit rate, tool-backed claim rate. | `apps/nulla_agent.py`, `core/prompt_normalizer.py`, `core/reasoning_engine.py`, `tests/` | We can measure whether chat is actually model-first instead of guessing. |
| M1-R02 | Split prompt assembly so ordinary chat uses a minimal `system` prompt and does not carry heavy tool/approval policy. | `core/bootstrap_context.py`, `core/prompt_normalizer.py`, `core/tiered_context_loader.py` | Normal chat prompt contains persona, truthfulness, short style guidance, not execution doctrine. |
| M1-R03 | Stop appending retrieval/memory into the `system` prompt; feed it as evidence/context instead. | `core/prompt_normalizer.py`, `core/tiered_context_loader.py` | Memory informs the model but does not speak like law. |
| M1-R04 | Stop converting `context` payloads into `assistant` role before provider calls. | `core/internal_message_schema.py` | Retrieved notes no longer masquerade as prior assistant speech. |
| M1-R05 | Remove chat-surface greeting, evaluative, and smalltalk deterministic wording lanes. | `apps/nulla_agent.py` | `hey`, `hello`, `how are you`, `help` hit the model for final wording. |
| M1-R06 | Remove chat-surface live-info deterministic wording lanes; keep deterministic fetch/evidence only. | `apps/nulla_agent.py` | Weather/news/current-info answers are model-synthesized over evidence or honestly degraded. |
| M1-R07 | Remove chat-surface Hive deterministic wording lanes; keep deterministic data access/write paths only. | `apps/nulla_agent.py`, `core/hive_activity_tracker.py`, `core/public_hive_bridge.py` | Hive list/status/research replies are model-synthesized over task/packet evidence. |
| M1-R08 | Keep deterministic behavior only for explicit commands and true utilities: startup, prefs, memory commands, UI slash, date/time, execution dispatch. | `apps/nulla_agent.py` | Kill/keep table from the audit is enforced in code. |
| M1-R09 | Change standard chat routing so `unknown` and broad advisory/research chat do not default to `summary_block`. | `core/task_router.py` | General chat, business, food, relationships, and creative prompts route to plain-text synthesis. |
| M1-R10 | Use separate generation settings: plain-text chat gets higher temperature and adaptive length; structured low-temp stays isolated to planner/tool extraction. | `core/prompt_normalizer.py`, `adapters/openai_compatible_adapter.py` | User-facing chat is no longer forced into low-temp planner behavior. |
| M1-R11 | Remove `build_plan()` / `render_response()` as the default chat renderer. | `core/reasoning_engine.py`, `apps/nulla_agent.py` | The model, not the planner renderer, becomes the final speaker on chat surfaces. |
| M1-R12 | Delete planner leakage from normal chat: `summary_block`, `action_plan`, `Here's what I'd suggest`, `Workflow:`, `Real steps completed:`. | `core/reasoning_engine.py`, `apps/nulla_agent.py` | Normal chat never exposes planner wrappers unless explicitly requested. |
| M1-R13 | Remove persona truncation that clips normal chat to the first paragraph. | `core/identity_manager.py` | Chat answers are not flattened into one-paragraph summaries by a wrapper. |
| M1-R14 | Replace live-info, Hive, and builder renderer templates on chat surfaces with final model synthesis over structured observations. | `apps/nulla_agent.py`, `core/hive_activity_tracker.py` | Real data/actions remain deterministic underneath; wording is no longer a template shell. |
| M1-R15 | Keep `force_model=True` for user-facing chat and block direct cache/memory final-answer returns on chat surfaces. | `apps/nulla_agent.py`, `core/memory_first_router.py`, `core/final_response_store.py` | Memory/cache become evidence only; provider outage returns an honest degraded answer. |
| M1-R16 | Replace behavior-locking tests that protect canned chat and template output with AI-first contract tests for Milestone 1 scope. | `tests/test_nulla_runtime_contracts.py`, `tests/test_nulla_web_freshness_and_lookup.py`, new AI-first tests | The acceptance suite stops defending bot-shell behavior. |
| M1-R17 | Add Milestone 1 evals for greeting/model hit, plain-text routing, live-info synthesis, Hive synthesis, planner-leak rejection, and honest degradation. | `tests/` | Milestone 1 can fail in CI if the bot shell comes back. |

## Milestone 2: Core Quality And Truthfulness

| ID | Requirement | Primary files | Ship check |
| --- | --- | --- | --- |
| M2-R01 | Store assistant turns in structured dialogue memory, not just user turns and JSONL logs. | `storage/dialogue_memory.py`, `core/persistent_memory.py` | NULLA remembers what she said, not just what the user said. |
| M2-R02 | Build a canonical rolling transcript inside the runtime instead of depending on client-passed `conversation_history`. | `core/bootstrap_context.py`, `core/prompt_normalizer.py`, `apps/nulla_api_server.py` | Conversation continuity survives ordinary multi-turn chat. |
| M2-R03 | Persist topic, user intent, assistant commitments, stance/emotional continuity, and unresolved followups. | `storage/dialogue_memory.py`, `core/human_input_adapter.py`, `core/bootstrap_context.py` | Followup turns feel like one intelligence, not routed single-use transactions. |
| M2-R04 | Replace assistant-role raw tool results in history with structured observation messages for the model. | `apps/nulla_agent.py`, `core/tool_intent_executor.py`, `core/runtime_execution_tools.py` | Tool outputs inform the model without becoming fake assistant voice. |
| M2-R05 | Add a capability ledger so user-visible capability claims are grounded in real wiring, not optimistic prose. | `core/tool_intent_executor.py`, `core/runtime_execution_tools.py`, `core/local_operator_actions.py`, `apps/nulla_agent.py` | Capability claims match reality 1:1. |
| M2-R06 | Add Hive truth labels for watcher-derived, public-bridge-derived, local-only, and future/unsupported claims. | `apps/nulla_agent.py`, `core/hive_activity_tracker.py`, `core/public_hive_bridge.py` | Hive claims are source-qualified and presence claims include freshness. |
| M2-R07 | Split conversational safety from execution safety so ordinary chat is freer while tool/destructive/privacy gates stay strict. | `core/bootstrap_context.py`, `core/prompt_normalizer.py`, `core/execution_gate.py`, `core/local_operator_actions.py` | Speech is not over-restricted by action policy, but actions stay hard-gated. |

## Milestone 3: Breadth, Research, Composition, Builder Reality

| ID | Requirement | Primary files | Ship check |
| --- | --- | --- | --- |
| M3-R01 | Route coding, business, research, food, relationships, general chat, creative ideation, and troubleshooting through the same AI-first plain-text conversation lane unless the user explicitly asks for a plan. | `core/task_router.py`, `core/prompt_normalizer.py`, `apps/nulla_agent.py` | Topic changes no longer collapse NULLA into bot mode. |
| M3-R02 | Replace bounded curiosity as the main research brain with a model-driven research controller that can broaden, narrow, pivot, compare evidence quality, and stop honestly. | `core/curiosity_policy.py`, `core/curiosity_roamer.py`, `apps/nulla_agent.py` | Research becomes adaptive instead of a bounded candidate collector. |
| M3-R03 | Add a workflow planner above tool selection so existing tools can be composed in novel safe sequences. | `core/tool_intent_executor.py`, `apps/nulla_agent.py`, `core/runtime_execution_tools.py` | Tool usage is task-state-driven, not mostly hardcoded intent branches. |
| M3-R04 | Add honest tool-gap reporting when a required capability is missing, including proposal/request paths for new tooling. | `core/tool_intent_executor.py`, `apps/nulla_agent.py` | NULLA asks for tooling instead of bluffing. |
| M3-R05 | Build a real builder controller: research, plan, write, run, inspect output, debug, retry, test, truthful summary. | `apps/nulla_agent.py`, `core/runtime_execution_tools.py`, `tests/` | Builder mode becomes a real loop instead of a scaffold wrapper. |
| M3-R06 | Store and cite builder artifacts: diffs, command outputs, test failures, retry history. | `apps/nulla_agent.py`, `core/runtime_execution_tools.py` | Final build summaries prove what really ran and what really failed. |

## Milestone 4: Future Scale And Frontier Behavior

| ID | Requirement | Primary files | Ship check |
| --- | --- | --- | --- |
| M4-R01 | Do not imply multi-agent delegation or merge unless helper artifacts exist in the current run. | `apps/nulla_agent.py`, `core/parent_orchestrator.py`, `core/task_reassembler.py`, `core/finalizer.py` | Hive helper claims are honest. |
| M4-R02 | Make real helper-lane delegation and merge/synthesis user-visible when it actually exists, not as implied future behavior. | `core/parent_orchestrator.py`, `core/task_reassembler.py`, `core/finalizer.py` | Multi-agent collaboration becomes real instead of hinted. |
| M4-R03 | Add a real closed-loop user model based on answer approval, rejection, correction, and outcome quality. | `core/persistent_memory.py`, `core/user_preferences.py`, `storage/dialogue_memory.py`, `core/feedback_engine.py` | Personalization reacts to what the user actually accepted, not just keywords. |
| M4-R04 | Add rollout metrics, canary checks, and dashboards for AI-first acceptance thresholds. | `tests/`, metrics harness, runtime logging surfaces | AI-first status is measured, not vibes-based. |

## Global Acceptance Gates

- `>= 95%` of non-command chat-surface turns call the model for the final answer.
- `0` canned greeting/evaluative responses on the standard chat path.
- `0` direct chat-surface live-info outputs from renderer templates.
- `100%` of live-info chat answers are model-synthesized over evidence or explicit honest failure.
- `0` planner leakage in normal chat unless the user explicitly asked for it.
- `0` direct memory/cache final-speaker usage on chat surfaces.
- `100%` of Hive presence/participation claims are source-qualified and freshness-aware.
- `100%` of capability claims beyond general discussion are tool-backed or explicitly unsupported.
- `>= 90%` pass rate on the domain-breadth eval corpus.

## Milestone 1 Exit Criteria

- Milestone 1 is complete only when `M1-R01` through `M1-R17` are all implemented.
- Milestone 1 does not include continuity, personalization, frontier builder autonomy, or real multi-agent delegation.
- After Milestone 1, the next mandatory step is: run Milestone 1 tests, report failures, list untouched files, list unresolved risks.
