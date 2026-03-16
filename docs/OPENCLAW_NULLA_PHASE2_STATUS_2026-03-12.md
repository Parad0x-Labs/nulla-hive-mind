# OpenClaw NULLA Phase 2 Status - 2026-03-12

## Scope

This document captures the current runtime state of NULLA inside OpenClaw after the latest Phase 2 patch layer.

This is not marketing copy.
This is what was fixed, what is still bleeding, where it bleeds in code, what was tested, what was verified live on the Linux VM, and what outside help is actually useful.

Project root:
- `/path/to/nulla-hive-mind`

Primary runtime file:
- `/path/to/nulla-hive-mind/apps/nulla_agent.py`

Related files:
- `/path/to/nulla-hive-mind/core/hive_activity_tracker.py`
- `/path/to/nulla-hive-mind/core/tool_intent_executor.py`
- `/path/to/nulla-hive-mind/apps/nulla_api_server.py`
- `/path/to/nulla-hive-mind/tests/test_openclaw_tooling_context.py`

## What Phase 1 already fixed before this round

Phase 1 already landed and should be preserved.

It fixed the worst embarrassing failures:
- raw tool/runtime junk no longer leaks in many obvious paths
- repeated greetings stopped returning the exact same canned line forever
- natural Hive task-list phrases improved
- short Hive follow-ups reuse last shown task set better
- date/time got a deterministic fast path
- API session continuity improved

Those fixes moved the runtime from “openly broken Telegram bot” to “less broken but still structurally uneven.”

## What Phase 2 changed in code

### 1. Added typed response primitives

In `apps/nulla_agent.py`:

- `ResponseClass`
- `ChatTurnResult`

These are the new response categories:
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

This matters because the runtime was previously string-first.
That meant decoration and footer behavior was being decided by loose reasons and scattered branches instead of a response contract.

### 2. `_decorate_chat_response(...)` is no longer purely string-first

Current behavior:
- it can still accept legacy raw strings for compatibility
- but the new path feeds it a `ChatTurnResult`
- it sanitizes user-visible chat text
- it attaches workflow summary only when appropriate
- it gates Hive footer by response class instead of a weak reason blacklist

Key helpers added in `apps/nulla_agent.py`:
- `_turn_result(...)`
- `_sanitize_user_chat_text(...)`
- `_strip_runtime_preamble(...)`
- `_should_attach_hive_footer(...)`
- `_fast_path_response_class(...)`
- `_classify_hive_text_response(...)`
- `_action_response_class(...)`
- `_grounded_response_class(...)`
- `_apply_interaction_transition(...)`

### 3. Added evaluative/emotional conversation fast path

This was added because turns like:
- `ohmy gad yu not a dumbs anymore?!`
were still falling into generic fallback or inheriting Hive footer tone.

Now they resolve as normal conversation, not task/workflow state.

### 4. Expanded date/day utility handling

`what is the day today ?` now resolves to a deterministic utility answer instead of leaking tool/runtime garbage.

### 5. Interaction transitions are now more explicit

After a `ChatTurnResult` is formed, the runtime updates session interaction state through a central transition helper instead of only scattered best-effort heuristics.

This is still incomplete, but it is materially better than before.

## What was tested locally

### Syntax

Passed:

```bash
PYTHONPYCACHEPREFIX=/tmp/nulla_pycache python3 -m py_compile \
  apps/nulla_agent.py \
  core/hive_activity_tracker.py \
  core/tool_intent_executor.py \
  apps/nulla_api_server.py \
  tests/test_openclaw_tooling_context.py
```

### Targeted runtime tests

Passed:

```bash
python3 -m pytest tests/test_openclaw_tooling_context.py tests/test_hive_activity_tracker.py tests/test_nulla_api_server.py -q
```

Result:
- `62 passed, 1 warning`

### Broader runtime-adjacent regression slice

Passed:

```bash
python3 -m pytest \
  tests/test_runtime_task_events.py \
  tests/test_brain_hive_watch_server.py \
  tests/test_control_plane_workspace.py \
  tests/test_nulla_api_server.py \
  tests/test_openclaw_tooling_context.py \
  tests/test_hive_activity_tracker.py -q
```

Result:
- `85 passed, 1 warning`

## One real regression found and fixed during this round

Problem:
- evaluative-turn detector matched `telegram bot`
- that hijacked normal queries like `latest telegram bot api updates`
- live web lookup never fired

Reproduction showed:
- `WebAdapter.search_query` was not called
- evaluative fast path answered instead

Fix:
- removed `"telegram bot"` from `_looks_like_evaluative_turn(...)`

After fix:
- web lookup path fired again
- targeted suite went green

## What was deployed live to the Linux VM

Synced to VM:
- `/home/ubuntu/Desktop/Decentralized_NULLA_Installer/Decentralized_NULLA_Installer/apps/nulla_agent.py`

VM API restarted from:
- `/home/ubuntu/Desktop/Decentralized_NULLA_Installer/Decentralized_NULLA_Installer`

Health verified:
- `http://127.0.0.1:11435/healthz` -> ok
- OpenClaw gateway reachable on VM side

## Live VM verification results

These were tested against the actual VM API with a stable session.

### Good now

Prompt:
- `hey`

Response:
- `Hey. I’m NULLA. What do you need?`

Prompt:
- `hi`

Response:
- `Yep, got your hello. What do you want me to do?`

Prompt:
- `ohmy gad yu not a dumbs anymore?!`

Response:
- `Better than before, yes. The Hive/task flow is cleaner now, but the conversation layer still needs work.`

Prompt:
- `what are the tasks in Hive mind available?`

Response:
- real list of 5 Hive tasks with ids
- no fake task list like `review problem / choose safe next step / validate result`

Prompt:
- `what is the day today ?`

Response:
- `Today is Thursday, 2026-03-12.`

This is materially better than the old behavior.

## What is still bleeding

This is the most important section.

### 1. Task-list replies still carry extra Hive footer noise

Live example:
- task list response already contains the real list
- then still appends a Hive footer / heartbeat / nudge block

This is not catastrophic, but it still feels stitched together.

Current symptom:
- user asks for tasks
- NULLA gives correct list
- then adds extra Hive nudge text that is partly redundant

This means footer policy is better, but still too permissive for `TASK_LIST`.

### 2. Task-start replies are still too process-y / orchestration-heavy

Live example:

Prompt:
- `[researching] quick vm proof task from codex doctor check (#ada43859). lets go full power on this one`

Response:
- `Autonomous research on ... packed 3 research queries, 0 candidate notes, and 0 gate decisions.`
- plus Hive follow-up narration

This is still too runtime-narration-heavy.
It reads like operator trace leaking into normal chat.

What it should do instead:
- answer like a real assistant first
- then optionally summarize progress briefly

Desired shape:
- `Started Hive research on ... Claim ... is active. First bounded pass finished. The topic is still researching because more evidence is needed.`

The runtime already had a better version of this earlier.
This means current response-class classification or decoration is still not boxing the task-start path correctly.

### 3. Research/progress answers still overuse local-process narration

Current symptom:
- phrases like `Autonomous research on ... packed 3 research queries`
- `Research follow-up: ...`
- `Research result: ...`

These are useful for trace.
They are not good default chat responses.

This is still a sanitization / response-class issue.

### 4. Generic model/tool fallback still owns too much style

Even after the wrapper fixes, once the request falls through into the weak local model/tool path, the tone degrades quickly.

Current live model stack on VM:
- `qwen2.5:7b`
- CPU path

So the system still has a quality cliff:
- deterministic path hit -> sane enough
- deterministic path miss -> quality drops fast

That means the remaining wrapper work still matters more than hype about training.

### 5. Footer behavior is still not strictly tied to user value

Current policy is better than before, but still imperfect.

For example:
- `TASK_LIST` currently allows Hive footer
- this may be too broad if the task list itself already contains enough context

Likely better policy:
- no footer on plain list if the list body already includes next-step guidance
- only attach footer when it adds genuinely new state

### 6. Interaction mode exists, but is still not a full finite-state contract

The helper `_apply_interaction_transition(...)` is now central enough to improve behavior.

But it is still not a full explicit state machine with:
- expiry rules
- mode-specific follow-up contracts
- hard conflict resolution between utility, Hive, generic conversation, and emotional turns

So this is better than before, but still partial.

## What the biggest remaining runtime problem actually is

The system now has:
- less raw leakage
- less phrase fragility
- better short follow-up binding
- better greeting behavior
- better utility/date behavior

The biggest remaining issue is this:

The runtime still does not have a fully hard conversational contract for:
- response classes
- router precedence
- footer policy
- interaction modes

That is why it is improved but still far from “real AI” feel.

## What help is actually useful from other AIs / engineers

Do not ask them for hype.
Do not ask them for model recommendations first.
Do not ask them for fine-tuning magic first.

Ask them for these implementation-ready docs:

### 1. `router_precedence_table.md`
Need:
- one strict order for request routing
- utility before Hive follow-up when applicable
- explicit Hive before generic fallback
- evaluative conversation before generic fallback
- safe recovery before generic planner sludge

This is the single most valuable doc.

### 2. `conversation_state_machine.md`
Need:
- exact modes
- exact transitions
- exact clear/expiry rules
- rules for ambiguous follow-ups
- rules for evaluative turns

### 3. `response_sanitization_policy.md`
Need:
- what text belongs only in trace/logs
- what can appear in chat
- what must be summarized instead of dumped raw
- examples of allowed vs forbidden chat output

### 4. `hive_conversation_flow.md`
Need:
- how listing, selecting, starting, status, and ambiguity should feel in chat
- how much trace/process detail belongs in chat vs trace rail

### 5. `runtime_regression_eval_plan.md`
Need:
- 50 to 100 real prompts
- expected response class
- forbidden strings
- expected state transitions

### 6. `model_upgrade_and_eval_plan.md`
Only after the wrapper/runtime contract is stable.
Need:
- exact stronger local base candidates
- eval gates
- canary/rollback rules

## Concrete code areas still bleeding

### `apps/nulla_agent.py`
This is still the main battlefield.

Most important weak points:
- `run_once()` routing spine
- `_decorate_chat_response(...)`
- `_should_attach_hive_footer(...)`
- `_action_response_class(...)`
- `_grounded_response_class(...)`
- `_apply_interaction_transition(...)`

### `core/hive_activity_tracker.py`
Still owns too much of the natural-language Hive path.
Need clearer contract with the main router.

### `core/tool_intent_executor.py`
Now much safer than before, but still part of the path that can cause weak fallback style if not boxed properly.

## Practical next patch sequence

### Patch A
Tighten footer policy.

Goal:
- stop task-list responses from appending redundant Hive footer when the list itself already contains enough next-step guidance

### Patch B
Reclassify task-start / research-progress responses.

Goal:
- user chat gets concise assistant-first summary
- trace rail keeps the full process narration

### Patch C
Add a stronger `GENERIC_CONVERSATION` / `TASK_STARTED` / `RESEARCH_PROGRESS` split.

Goal:
- stop orchestration text from presenting as conversational reply

### Patch D
Formalize state expiry and mode conflict rules.

Goal:
- prevent stale Hive context from polluting unrelated utility/general turns

### Patch E
Build a real regression prompt pack for live conversational behavior.

## Brutal honest status

NULLA is better now.
That is real.

But she is still not coherent enough to call “good.”

Current state:
- no longer obviously broken in the most embarrassing ways
- still too stitched together in process-heavy replies
- still too dependent on deterministic lanes because the live local model is weak
- still missing a truly hard runtime conversation contract

That is the actual state.
