# HANDOVER - 2026-03-12 - NULLA / OpenClaw / Hive / Adaptation

This document is the latest handover snapshot for the repo at:

`/path/to/nulla-hive-mind`

It is written to let the next agent continue without rereading the entire chat.

This is not hype. It is the real state.

---

## 1. What We Are Building

NULLA is becoming a local-first AI runtime with these major layers:

1. OpenClaw as the operator shell / chat UI
2. NULLA runtime as the control/orchestration layer
3. Hive / Brain Hive as the shared task / claim / post / research substrate
4. Liquefy as the dense artifact / compression / sharable knowledge lane
5. Adaptation / LoRA loop as the self-improvement spine
6. Watch / Trace / control-plane visibility so the runtime is inspectable
7. Cross-platform installer flow for macOS / Linux / Windows

The intended end-state is:
- one-command install
- OpenClaw launches with NULLA wired in as the default brain
- Liquefy available by default
- Hive / Watch / Trace visible by default
- durable useful outputs feed the adaptation loop
- Commons becomes a filtered funnel into real Hive research
- personal AI + collective Hive intelligence + adaptive improvement

The hard truth:
- the architecture direction is real
- the product is still uneven
- the wrapper/control layer is much improved, but not finished
- the local model is still weaker than frontier hosted models
- the adaptation loop exists structurally, but the model quality bottleneck is still very real

---

## 2. High-Level Current Status

### Stable enough / materially improved
- raw tool/runtime junk leakage is much lower than before
- greetings are less bot-like
- repeated greeting loop is reduced
- evaluative turns are better than before
- natural Hive task listing works much better
- short Hive follow-ups bind better
- date/day utility answers work directly
- task-start classification for raw `Autonomous research on ...` is fixed
- trace/watch/control-plane surfaces exist
- durable `useful_outputs` signal layer exists
- adaptation loop is structured-first rather than chat-first
- installer doctor exists

### Still not good enough
- router precedence is still too implicit
- interaction state is improved but not a full finite-state contract
- some task/progress replies still feel too runtime-shaped in some paths
- response sanitization/user-chat-vs-trace boundary is better, not perfect
- generic fallback still degrades badly when deterministic routing misses
- the live model is still weak enough that bad fallback hurts a lot
- Commons/social-to-research funnel exists only partially and is not fully validated
- trustless DNA / economics are still not real
- final one-command consumer-grade install is not done
- not ready to claim “internal testing ready” as a polished product

---

## 3. The Most Important Runtime Story Right Now

There were two big runtime phases.

### Phase 1 - obvious brokenness
This was mostly fixed already:
- stop leaking raw internal junk like `invalid tool payload`
- widen Hive list/pull phrasing
- improve repeated greeting behavior
- improve short Hive follow-up reuse
- fix direct utility fast paths like date/day
- reduce stupid exact-phrase dependence

### Phase 2 - structural contract
This is still incomplete.

The remaining issue is no longer the old “bot totally broken” state.
The current issue is that the runtime still lacks a strong enough conversation contract.

What still needs hardening:
1. strict router precedence
2. explicit interaction state machine with expiry/clear rules
3. stronger response sanitization contract
4. stronger task-start / progress chat contract
5. larger conversational regression pack

This means the next work is not “more regex hacks” and not “just swap model.”
It is the runtime contract in `apps/nulla_agent.py`.

---

## 4. Major Things Already Built Before This Latest Runtime Slice

### 4.1 Durable useful-output signal layer
Files:
- `/path/to/nulla-hive-mind/storage/migrations.py`
- `/path/to/nulla-hive-mind/storage/useful_output_store.py`

What exists:
- canonical `useful_outputs` table
- durable training signal over runtime truth
- mirrors:
  - accepted/reviewed `task_results`
  - successful `finalized_responses`
  - approved/evidence-backed `hive_posts`
- rows carry:
  - source type / source id
  - task / topic / claim / result lineage
  - artifact ids
  - acceptance state
  - review state
  - archive state
  - eligibility reasons
  - durability reasons
  - quality score

Purpose:
- stop adaptation from treating random chat as equal to accepted work

### 4.2 Structured-first adaptation loop
Files:
- `/path/to/nulla-hive-mind/core/adaptation_dataset.py`
- `/path/to/nulla-hive-mind/core/adaptation_autopilot.py`
- `/path/to/nulla-hive-mind/storage/adaptation_store.py`
- `/path/to/nulla-hive-mind/core/lora_training_pipeline.py`
- `/path/to/nulla-hive-mind/adapters/peft_lora_adapter.py`
- `/path/to/nulla-hive-mind/core/policy_engine.py`
- `/path/to/nulla-hive-mind/config/default_policy.yaml`

What exists:
- corpus build -> score -> train -> eval -> reject/promote
- min thresholds:
  - structured examples
  - high-signal examples
  - max conversation ratio
- candidate must beat baseline to promote

Hard truth:
- structurally real
- still often blocked by corpus quality and volume

### 4.3 Trace / watch / control-plane surfaces
Files:
- `/path/to/nulla-hive-mind/core/runtime_task_rail.py`
- `/path/to/nulla-hive-mind/core/control_plane_workspace.py`
- `/path/to/nulla-hive-mind/core/brain_hive_dashboard.py`
- `/path/to/nulla-hive-mind/apps/nulla_api_server.py`

What exists:
- task / claim / topic visibility
- adaptation blocker visibility
- useful output counts
- Hive budget / swarm budget visibility
- trace rail endpoint

### 4.4 Installer doctor and product shell groundwork
Files:
- `/path/to/nulla-hive-mind/installer/doctor.py`
- `/path/to/nulla-hive-mind/installer/install_nulla.sh`
- `/path/to/nulla-hive-mind/installer/install_nulla.bat`
- `/path/to/nulla-hive-mind/installer/write_install_receipt.py`
- `/path/to/nulla-hive-mind/NULLA_STARTER_KIT.md`

What exists:
- doctor report at install time
- better receipts / staging info
- trace URL in receipt
- staged trainable base path in bundle

What is not done:
- full release-manifest/profile installer rewrite
- final one-command polished consumer flow

### 4.5 Commons / social-to-research slice
Files touched in earlier Commons slice:
- `/path/to/nulla-hive-mind/core/brain_hive_models.py`
- `/path/to/nulla-hive-mind/storage/migrations.py`
- `/path/to/nulla-hive-mind/storage/brain_hive_store.py`
- `/path/to/nulla-hive-mind/core/brain_hive_service.py`
- `/path/to/nulla-hive-mind/apps/meet_and_greet_server.py`
- `/path/to/nulla-hive-mind/core/brain_hive_dashboard.py`
- `/path/to/nulla-hive-mind/core/control_plane_workspace.py`
- `/path/to/nulla-hive-mind/storage/useful_output_store.py`

What exists:
- endorsement models
- comment models
- promotion candidate models
- promotion review models
- DB tables for Commons promotions/comments/endorsements
- service methods and API routes
- dashboard promotion queue view

Hard truth:
- still partial
- needs more tests
- not deployed/validated enough to call finished

---

## 5. Runtime Fixes Already Landed In `apps/nulla_agent.py`

Current important file:
- `/path/to/nulla-hive-mind/apps/nulla_agent.py`

What is already there now:

### Typed response contract exists
- `ResponseClass` enum exists
- `ChatTurnResult` dataclass exists
- `_decorate_chat_response(...)` already accepts `ChatTurnResult | str`

Classes currently present:
- `SMALLTALK`
- `UTILITY_ANSWER`
- `TASK_LIST`
- `TASK_SELECTION_CLARIFICATION`
- `TASK_STARTED`
- `TASK_STATUS`
- `TASK_FAILED_USER_SAFE`
- `RESEARCH_PROGRESS`
- `APPROVAL_REQUIRED`
- `SYSTEM_ERROR_USER_SAFE`
- `GENERIC_CONVERSATION`

### User-facing shaping exists
- `_shape_user_facing_text(...)` rewrites task-start/progress text
- `_sanitize_user_chat_text(...)` blocks common leaked internals
- `_should_attach_hive_footer(...)` already heavily tightened compared with older state

### Known better behavior already live
- repeated greetings are not identical loops anymore
- evaluative turn like `ohmy gad yu not a dumbs anymore?!` stays conversational
- `what is the day today ?` answers directly
- Hive list requests behave much better
- raw `Autonomous research on ...` start text is now classified as `TASK_STARTED`

---

## 6. What Was Fixed Most Recently In Runtime

### 6.1 Remaining live task-start classification gap
This was the last obvious live blocker that got fixed.

Problem:
- one Hive task-start path still returned raw runtime narration:
  - `Autonomous research on ... packed 3 research queries...`

Cause:
- `hive_research_followup` classification only treated `Started Hive research on ...` as `TASK_STARTED`
- raw `Autonomous research on ...` escaped the shaping layer

Fix:
- in `apps/nulla_agent.py`, `hive_research_followup` classification now maps:
  - `Autonomous research on ...` -> `TASK_STARTED`
  - `Research follow-up:` / `Research result:` -> `RESEARCH_PROGRESS`

Live verified result after VM sync/restart:
- old bad output disappeared
- new output became:
  - `Started Hive research on ... First bounded pass is underway.`

### 6.2 Footer policy is no longer the main fire
Current footer behavior is already much tighter.

The app layer no longer broadly appends Hive footer to everything.
This materially reduced the stitched-together feeling.

Hard truth:
- footer policy is improved
- it still needs stronger user-value criteria in the final contract
- but it is no longer the main blocker

---

## 7. What Is Still Broken Right Now

This is the real remaining runtime problem set.

### 7.1 Router precedence is still too implicit
Current state:
- `run_once()` still routes through inline branch order
- precedence is not centralized into one clear routing contract

Why this matters:
- behavior still feels accidental in edge cases
- deterministic routing wins are real, but misses still degrade badly

Needed:
- one explicit routing order in code

Target order:
1. utility hard overrides
2. active interaction-state follow-up
3. explicit Hive intents
4. UI/system utility intents
5. emotional/evaluative conversation
6. smalltalk/presence
7. Hive-adjacent safe recovery
8. generic tool/model path
9. final safe fallback

### 7.2 Conversation state is still not a real finite-state machine
Current state:
- `_apply_interaction_transition(...)` exists
- `interaction_mode` / `interaction_payload` exist in tracker state
- but transitions are still heuristic and too thin
- there is no strong expiry/clear contract

Why this matters:
- stale Hive context can still bleed into later turns
- ambiguity handling is still not deterministic enough

Needed:
- explicit modes
- central transitions
- expiry/clear logic
- conflict rules so utility/general turns don’t inherit stale Hive state

### 7.3 Response sanitization boundary is still too soft
Current state:
- much better than before
- no longer the old catastrophic leak problem
- but user chat, orchestration/process narration, and trace/debug truth are still not fully separated

Needed:
- stronger contract for:
  - what is allowed in chat
  - what stays in logs/trace only
  - what gets summarized into assistant-style language

### 7.4 Task-progress / task-start chat contract is still not strong enough
Current state:
- much better than raw runtime narration
- but some paths still feel too operator-ish or process-heavy

Needed:
- user-facing assistant-style contract for:
  - task started
  - first bounded pass finished
  - still researching
  - blocked
  - solved

### 7.5 Generic fallback still hurts hard
Current state:
- local model still weak enough that once deterministic routing misses, quality falls off fast

Implication:
- the wrapper/runtime contract must be strong enough that generic model fallback is last and boxed in

---

## 8. Files That Matter Most For The Next Agent

### Primary battlefields
- `/path/to/nulla-hive-mind/apps/nulla_agent.py`
- `/path/to/nulla-hive-mind/core/hive_activity_tracker.py`
- `/path/to/nulla-hive-mind/core/tool_intent_executor.py`

### Tests that should be touched next
- `/path/to/nulla-hive-mind/tests/test_openclaw_tooling_context.py`
- `/path/to/nulla-hive-mind/tests/test_hive_activity_tracker.py`
- `/path/to/nulla-hive-mind/tests/test_nulla_api_server.py`

### Supporting docs already written
- `/path/to/nulla-hive-mind/docs/OPENCLAW_NULLA_RUNTIME_FAILURE_FLOW_2026-03-11.md`
- `/path/to/nulla-hive-mind/docs/OPENCLAW_NULLA_RUNTIME_HELP_NEEDED_2026-03-11.md`
- `/path/to/nulla-hive-mind/docs/OPENCLAW_NULLA_PHASE2_STATUS_2026-03-12.md`
- `/path/to/nulla-hive-mind/docs/OPENCLAW_NULLA_HELP_REQUEST_2026-03-12.md`
- `/path/to/nulla-hive-mind/docs/OPENCLAW_NULLA_CODEX_PHASE2_BRIEF_2026-03-12.md`
- `/path/to/nulla-hive-mind/docs/HANDOVER_2026-03-10_SIGNAL_COMMONS_ADAPTATION.md`
- `/path/to/nulla-hive-mind/docs/NULLA_HIGHLIGHTS_2026-03-11.md`

---

## 9. What Was Tested And What The Results Were

### Runtime-focused slices that were green before this handover
Commands previously run and verified:

```bash
python3 -m pytest tests/test_openclaw_tooling_context.py tests/test_hive_activity_tracker.py tests/test_nulla_api_server.py -q
```
Result previously reported:
- `67 passed, 1 warning`

```bash
python3 -m pytest tests/test_runtime_task_events.py tests/test_brain_hive_watch_server.py tests/test_control_plane_workspace.py tests/test_nulla_api_server.py tests/test_openclaw_tooling_context.py tests/test_hive_activity_tracker.py -q
```
Result previously reported:
- `88 passed, 1 warning`

### Syntax/compile validation
Previously reported passed with:

```bash
PYTHONPYCACHEPREFIX=/tmp/nulla_pycache python3 -m py_compile apps/nulla_agent.py tests/test_openclaw_tooling_context.py
```

### Older signal/adaptation/control-plane slices that were green before Commons/runtime churn
Previously reported:
- `47 passed, 1 warning`
- `31 passed, 1 warning`
- `62 passed`
- `65 passed, 1 warning`
- `85 passed`

Treat those as historical checkpoints, not current blanket truth.
The next agent should rerun the exact slices after any new Phase 2 work.

---

## 10. Live VM State Last Known

The VM runtime was previously synced and restarted after the latest task-start classification fix.

Last known good live checks:
- greetings better
- evaluative turns better
- date/day direct answers okay
- Hive list prompts okay
- raw `Autonomous research on ...` task-start output no longer leaked in the tested route

Hard truth:
- live VM was better than before
- but the full Phase 2 structural contract is still not implemented
- after new code changes, the next agent must resync and restart the VM API again

---

## 11. What Was NOT Implemented Yet In This Final Stretch

In the very last stretch before this handover, I was inspecting and planning the next code patch, but I did **not** land that next Phase 2 refactor yet.

I did **not** implement these yet:
- strict centralized router precedence in `run_once()`
- explicit interaction-state expiry/clear semantics
- tracker-side `updated_at` exposure for interaction state expiry
- stronger Hive-safe recovery path before generic fallback
- larger conversational regression pack for state/ambiguity/expiry

I inspected the real seams and confirmed the likely next edits, but did not patch them.

That means:
- repo code is at the last committed/patched runtime state described above
- the next Phase 2 structural refactor is still pending

---

## 12. The Exact Next Technical Steps

This is the clean next-agent order.

### Step 1 - patch tracker state to support expiry
In:
- `/path/to/nulla-hive-mind/core/hive_activity_tracker.py`

Needed:
1. include `updated_at` in `session_hive_state(...)`
2. stamp `interaction_payload` with `_set_at` in `set_hive_interaction_state(...)`
3. add `updated_at` to `_default_state(...)`

Why:
- app-layer state machine needs reliable timestamps for expiry

### Step 2 - centralize routing precedence in `run_once()`
In:
- `/path/to/nulla-hive-mind/apps/nulla_agent.py`

Needed:
- one explicit `_route_by_precedence(...)` or equivalent
- order:
  1. utility hard overrides
  2. active interaction-state follow-up
  3. explicit Hive intents
  4. UI/system utility intents
  5. evaluative/emotional conversation
  6. smalltalk/presence
  7. Hive-safe recovery
  8. generic tool/model path
  9. final safe fallback

Why:
- stop routing from feeling accidental

### Step 3 - make interaction state a real finite-state contract
In:
- `/path/to/nulla-hive-mind/apps/nulla_agent.py`

Needed:
- explicit modes
- central transitions
- explicit clear/expiry rules
- conflict rules so utility/general turns don’t inherit stale Hive context

Important behavior:
- exactly one pending task + `yes / ok / do it` -> bind automatically
- multiple pending tasks + short follow-up -> re-list or clarify
- no silent default-pick when ambiguous

### Step 4 - strengthen sanitization / chat-vs-trace boundary
In:
- `/path/to/nulla-hive-mind/apps/nulla_agent.py`
- maybe secondary in `/path/to/nulla-hive-mind/core/tool_intent_executor.py`

Needed:
- stronger hard boundary between:
  - user chat
  - orchestration/process narration
  - trace/debug/runtime truth

### Step 5 - add regression tests for real conversation flow
Main file:
- `/path/to/nulla-hive-mind/tests/test_openclaw_tooling_context.py`

Needed prompts to cover:
- `what is the date today?`
- `what is the day today ?`
- `show me the open hive tasks`
- `what are the tasks in Hive mind available?`
- `what do we have online? any tasks in hive mind?`
- `yes`
- `ok`
- `pick one`
- `review the problem`
- `pull the hive task and lets do one?`
- `you sound weird`
- `why are you acting like this`
- `ohmy gad yu not a dumbs anymore?!`

Need assertions for:
- response class intent
- no forbidden strings
- footer behavior
- ambiguity handling
- stale-state handling
- no trace/debug leakage

### Step 6 - only after wrapper contract is better
Then reconsider:
- stronger local model path
- iMac trainable base rerun
- better promotion/canary loop results

Do **not** start there first.

---

## 13. What The Next Agent Should NOT Waste Time On

Do not do these first:
- hype docs
- UI cosmetics
- prompt tuning
- crypto/trustless architecture
- model shopping before runtime contract cleanup
- large refactors across unrelated files
- ripping out working Hive behavior just to simplify

The current bottleneck is runtime contract, not missing hype.

---

## 14. Current Repo Truth In One Screen

### Real enough to count as built
- OpenClaw-connected NULLA runtime
- Hive topics / claims / posts / moderation / watcher
- Liquefy integration for compressed artifacts/knowledge
- durable useful-output layer
- structured-first adaptation loop
- trace/control-plane visibility
- cross-platform installer groundwork + doctor
- better-than-before conversational runtime behavior

### Partial / still unstable
- full Commons funnel
- full Phase 2 conversation contract
- model upgrade proof through successful adapted candidate
- final one-command polished consumer installer
- trustless economics

### Not ready to claim
- polished internal-test-ready conversational product
- “world computer” finished product truth
- robust self-improving swarm that already got materially smarter in production

---

## 15. Final Brutal Status

We are no longer in the embarrassing totally-broken state.
That part improved materially.

But we are also not yet at “this feels like real AI” consistency.

The system now fails in a narrower, more structural way:
- precedence too implicit
- state too weak
- sanitization not hard enough
- task/progress chat contract not tight enough
- weak local model hurts badly when deterministic routing misses

That is the real latest status.

