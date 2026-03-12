# OpenClaw NULLA Help Request - 2026-03-12

## Purpose

This is the current help-request document for the NULLA/OpenClaw runtime.

It is grounded in the actual code and current live behavior, not in stale diagnosis.

This document is for engineers or other coding agents who need to help fix what is still broken.

Project root:
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA`

Primary file:
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/apps/nulla_agent.py`

Secondary files:
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/hive_activity_tracker.py`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/tool_intent_executor.py`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/autonomous_topic_research.py`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/apps/nulla_api_server.py`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/tests/test_openclaw_tooling_context.py`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/tests/test_hive_activity_tracker.py`

---

## What is already fixed

The current runtime is not in the old “completely broken Telegram bot” state anymore.

These things are materially better now:
- repeated greetings no longer loop the exact same canned reply
- evaluative turns like `ohmy gad yu not a dumbs anymore?!` are handled as conversation instead of falling into generic sludge
- natural Hive task-list requests work much better
- short follow-ups like `yes` and `ok` bind better to last shown Hive task sets
- direct utility questions like `what is the day today ?` answer directly
- footer behavior is stricter than before
- raw tool garbage leaks are much lower

Current local validation after latest patch:
- focused runtime slice: `65 passed, 1 warning`
- broader runtime slice: `88 passed, 1 warning`

So this is no longer a “nothing works” situation.

---

## What is still wrong now

This is the real remaining problem set.

### 1. Footer policy is still not fully aligned with user value

The current code now blocks Hive footer on more classes, but footer logic is still a runtime policy concern that can drift.

Current implementation in `apps/nulla_agent.py`:

```python
    def _should_attach_hive_footer(
        self,
        result: ChatTurnResult,
        *,
        source_context: dict[str, object] | None,
    ) -> bool:
        surface = str((source_context or {}).get("surface", "") or "").strip().lower()
        if surface not in {"channel", "openclaw", "api"}:
            return False
        return result.response_class in {
            ResponseClass.TASK_SELECTION_CLARIFICATION,
            ResponseClass.APPROVAL_REQUIRED,
        }
```

This is much better than before.

But help is still needed because footer attachment should be driven by a stronger contract than just a small allowlist. We need a full policy for:
- when footer adds value
- when footer becomes redundant noise
- when footer should be suppressed even if Hive state exists

### 2. Task-start and progress replies are still too runtime-shaped in at least one live route

This is the biggest remaining visible problem.

Current shaping logic in `apps/nulla_agent.py`:

```python
    def _shape_user_facing_text(self, result: ChatTurnResult) -> str:
        text = self._sanitize_user_chat_text(
            result.text,
            response_class=result.response_class,
        )
        if result.response_class == ResponseClass.TASK_STARTED:
            text = re.sub(
                r"^Autonomous research on\s+`?([^`]+)`?\s+packed\s+\d+\s+research queries,\s*\d+\s+candidate notes,\s*and\s*\d+\s+gate decisions\.?",
                r"Started Hive research on `\1`. First bounded pass is underway.",
                text,
                flags=re.IGNORECASE,
            )
            text = text.replace(
                "The first bounded research pass already ran and posted its result.",
                "The first bounded pass already landed.",
            )
            text = text.replace(
                "This fast reply only means the first bounded research pass finished.",
                "The first bounded pass finished.",
            )
            text = text.replace(
                "Topic stays `researching` because NULLA still needs more evidence before it can honestly mark the task solved.",
                "It is still open because the solve threshold was not met yet.",
            )
            text = text.replace(
                "The research lane is active.",
                "First bounded pass is underway.",
            )
            text = re.sub(r"\bBounded queries run:\s*\d+\.\s*", "", text)
            text = re.sub(r"\bArtifacts packed:\s*\d+\.\s*", "", text)
            text = re.sub(r"\bCandidate notes:\s*\d+\.\s*", "", text)
            return " ".join(text.split()).strip()
        if result.response_class == ResponseClass.RESEARCH_PROGRESS:
            text = re.sub(r"^Research follow-up:\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"^Research result:\s*", "Here’s what I found: ", text, flags=re.IGNORECASE)
            return " ".join(text.split()).strip()
        return text
```

Local tests for this are green.

But live VM verification still shows one route returning raw runtime narration:

Example live response:
- `Autonomous research on \`ada43859...\` packed 3 research queries, 0 candidate notes, and 0 gate decisions.`

That means at least one live path is still bypassing the intended chat-shaping layer.

### 3. One task-start route is still bypassing the expected `TASK_STARTED` shaping

Current fast-path classification in `apps/nulla_agent.py`:

```python
    def _fast_path_response_class(self, *, reason: str, response: str) -> ResponseClass:
        if reason in {"smalltalk_fast_path", "startup_sequence_fast_path"}:
            return ResponseClass.SMALLTALK
        if reason in {"date_time_fast_path", "ui_command_fast_path", "credit_status_fast_path", "memory_command", "user_preference_command"}:
            return ResponseClass.UTILITY_ANSWER
        if reason == "help_fast_path":
            return ResponseClass.TASK_SELECTION_CLARIFICATION
        if reason == "evaluative_conversation_fast_path":
            return ResponseClass.GENERIC_CONVERSATION
        if reason == "runtime_resume_missing":
            return ResponseClass.SYSTEM_ERROR_USER_SAFE
        if reason == "hive_activity_command":
            return self._classify_hive_text_response(response)
        if reason == "hive_research_followup":
            lowered = str(response or "").lower()
            if lowered.startswith("started hive research on"):
                return ResponseClass.TASK_STARTED
            if "multiple real hive tasks open" in lowered or "pick one by name" in lowered:
                return ResponseClass.TASK_SELECTION_CLARIFICATION
            if "couldn't map that follow-up" in lowered or "couldn't find an open hive task" in lowered:
                return ResponseClass.TASK_SELECTION_CLARIFICATION
            return ResponseClass.TASK_FAILED_USER_SAFE
        if reason == "hive_status_followup":
            return ResponseClass.TASK_STATUS
        return ResponseClass.GENERIC_CONVERSATION
```

Problem:
- if a Hive-start-like response comes back as `Autonomous research on ...` instead of `Started Hive research on ...`, this fast-path classifier does **not** label it as `TASK_STARTED`
- then shaping never happens
- then the user sees runtime narration

This is a real bug.

Current suspicion:
- one route returns the raw `AutonomousResearchResult.response_text` from `core/autonomous_topic_research.py`
- instead of the assistant-built `Started Hive research on ...` summary
- and the classifier misses it because it only checks `startswith("started hive research on")`

Relevant source of the raw text:

```python
# core/autonomous_topic_research.py
response_text = (
    f"Autonomous research on `{topic_id}` packed {len(query_results)} research queries, "
    f"{len(candidate_ids)} candidate notes, and {len(promotion_decisions)} gate decisions."
)
```

This text is useful for trace.
It is not good default chat.

### 4. Hive activity tracker still emits operator-style narration

Current strings in `core/hive_activity_tracker.py`:

```python
lines.append(
    f"Research follow-up: {len(new_local_topics)} new local research thread(s) were queued"
    f"{_format_examples(labels)}."
)

lines.append(
    f"Research result: {len(new_local_runs)} new local research result(s) landed"
    f"{_format_examples(labels)}."
)
```

These are useful for trace or an operator feed.
They are not ideal default chat text.

The runtime now shapes some of them, but only if response classification hits the right class.
So this is another place where the response-class contract still needs help.

### 5. Conversation state is improved, but still not a full finite-state contract

Current code has central interaction transition handling, which is a step up.

But it is still not a full explicit state machine with:
- hard expiry rules
- full conflict resolution between utility, Hive, evaluative conversation, and generic fallback
- formal mode transition tables

That means once deterministic routing misses, weak fallback can still own too much of the answer style.

### 6. Generic model/tool fallback still matters too much

The VM runtime is still on:
- `qwen2.5:7b`
- CPU

That means any case that misses deterministic routing drops into a much weaker language path than hosted frontier models.

This is not the first thing to fix.
But it is still a real limiting factor.

---

## Current live behavior snapshot

### Good now

Verified live on VM:
- `what are the tasks in Hive mind available?`
  - returns the real list cleanly
  - no redundant Hive footer after the latest footer tightening
- `what is the day today ?`
  - returns direct utility answer
- `ohmy gad yu not a dumbs anymore?!`
  - returns evaluative conversational answer
  - no Hive footer

### Still bad now

Verified live on VM:
- selecting a Hive task with a natural start phrase can still return:
  - `Autonomous research on ... packed 3 research queries, 0 candidate notes, and 0 gate decisions.`

That is the clearest remaining bug.

---

## What I need help with

Not hype.
Not model recommendations first.
Not generic “fine tune it.”
Not UI cosmetics.

I need implementation-ready help in these exact areas.

### 1. Router precedence contract

Need a real `router_precedence_table.md` with:
- exact order of routing in `run_once()`
- utility hard overrides
- active interaction-state follow-up
- explicit Hive intents
- UI/system utility
- evaluative/emotional conversation
- smalltalk
- Hive-safe ambiguity recovery
- generic tool/model fallback
- final safe fallback

This is still the highest-value document.

### 2. Conversation state machine

Need a real `conversation_state_machine.md` with:
- exact interaction modes
- exact transitions
- exact expiry/clear rules
- exact behavior for `yes / ok / do it / pick one`
- exact behavior for emotional turns after a Hive sequence

### 3. Response sanitization / response-class contract

Need a real `response_sanitization_policy.md` with:
- what text is allowed in user chat
- what text belongs only in trace/logs
- how to summarize process detail instead of dumping it raw
- exact rules for task-start and research-progress outputs

### 4. Task-start / progress chat contract

This is the most concrete current help request.

Need a dedicated doc for:
- how `task started` should sound
- how `first bounded pass complete` should sound
- how `still researching` should sound
- how `blocked` should sound
- how `solved` should sound
- what process detail belongs in chat vs trace rail

Because the current user-visible bug is exactly here.

### 5. Runtime regression eval pack

Need a real `runtime_regression_eval_plan.md` with:
- 50 to 100 actual prompts
- expected response class
- forbidden substrings
- expected state transition
- expected footer behavior

Must include prompts like:
- `what is the day today ?`
- `what are the tasks in Hive mind available?`
- `pull the hive task and lets do one?`
- `yes`
- `ok`
- `pick one`
- `you sound weird`
- `why are you acting like this`
- `ohmy gad yu not a dumbs anymore?!`
- exact natural task-start phrases

---

## Concrete likely patch direction

This is the most useful technical guidance to hand to another engineer.

### A. Fix fast-path classification for raw autonomous-research text

Current issue:
- `_fast_path_response_class(...)` only treats `started hive research on` as `TASK_STARTED`

Likely fix:
- also classify `Autonomous research on ... packed ...` as `TASK_STARTED`
- or better, intercept and normalize it before classification

### B. Audit all task-start paths and force them through one user-facing shaper

Current likely problem:
- not all task-start outputs are entering the same shaping/decorating path

Need:
- a trace of every route that can produce task-start/progress text
- a guarantee they all return a typed `ChatTurnResult`
- a guarantee they all pass through `_shape_user_facing_text(...)`

### C. Keep operator/runtime narration out of default chat

Texts like these should be trace-only by default:
- `Autonomous research on ... packed 3 research queries`
- `Research follow-up: ...`
- `Research result: ...`

Chat should get a short assistant-style summary instead.

### D. Keep footer policy strict

Footer should stay off for:
- `SMALLTALK`
- `UTILITY_ANSWER`
- `GENERIC_CONVERSATION`
- `SYSTEM_ERROR_USER_SAFE`
- `TASK_FAILED_USER_SAFE`
- plain `TASK_LIST`
- default `TASK_STARTED`
- default `RESEARCH_PROGRESS`

Footer should only survive where it adds real next-step value.

---

## Exact code seams that need inspection

### `apps/nulla_agent.py`
Focus on:
- `run_once()`
- `_fast_path_result(...)`
- `_action_fast_path_result(...)`
- `_decorate_chat_response(...)`
- `_shape_user_facing_text(...)`
- `_should_attach_hive_footer(...)`
- `_fast_path_response_class(...)`
- `_action_response_class(...)`
- `_grounded_response_class(...)`
- `_apply_interaction_transition(...)`

### `core/hive_activity_tracker.py`
Focus on:
- chat footer text
- watcher/research follow-up summary text
- operator narration leaking into user-facing responses

### `core/autonomous_topic_research.py`
Focus on:
- raw `Autonomous research on ...` response text
- whether that text should remain raw internal trace only

---

## What not to waste time on yet

Do not start with:
- changing the model first
- redesigning the UI first
- new social/commons features
- chain/crypto economics
- more random regex patches without a stronger contract

The remaining visible problem is still in the runtime conversation contract.

---

## Brutal honest status

NULLA is better now.
That is real.

But she is still not coherent enough to call “good.”

Current status:
- no longer openly embarrassing in the old ways
- still too trace-shaped in at least one task-start path
- still too dependent on deterministic routing because the local model is weak
- still missing a fully explicit conversation/runtime contract

That is the actual state.
