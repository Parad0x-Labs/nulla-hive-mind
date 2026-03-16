# NULLA/OpenClaw Phase 2 Codex Brief

Use this brief for the next Codex pass.

Related context docs:
- [/path/to/nulla-hive-mind/docs/OPENCLAW_NULLA_PHASE2_STATUS_2026-03-12.md](/path/to/nulla-hive-mind/docs/OPENCLAW_NULLA_PHASE2_STATUS_2026-03-12.md)
- [/path/to/nulla-hive-mind/docs/OPENCLAW_NULLA_HELP_REQUEST_2026-03-12.md](/path/to/nulla-hive-mind/docs/OPENCLAW_NULLA_HELP_REQUEST_2026-03-12.md)
- [/path/to/nulla-hive-mind/docs/OPENCLAW_NULLA_RUNTIME_HELP_NEEDED_2026-03-11.md](/path/to/nulla-hive-mind/docs/OPENCLAW_NULLA_RUNTIME_HELP_NEEDED_2026-03-11.md)
- [/path/to/nulla-hive-mind/docs/OPENCLAW_NULLA_RUNTIME_FAILURE_FLOW_2026-03-11.md](/path/to/nulla-hive-mind/docs/OPENCLAW_NULLA_RUNTIME_FAILURE_FLOW_2026-03-11.md)

## Mission
Patch the current NULLA/OpenClaw Phase 2 runtime.

Primary target:
- `/path/to/nulla-hive-mind/apps/nulla_agent.py`

Secondary targets only if clearly required:
- `/path/to/nulla-hive-mind/core/hive_activity_tracker.py`
- `/path/to/nulla-hive-mind/core/tool_intent_executor.py`
- `/path/to/nulla-hive-mind/core/autonomous_topic_research.py`
- `/path/to/nulla-hive-mind/tests/test_openclaw_tooling_context.py`
- `/path/to/nulla-hive-mind/tests/test_hive_activity_tracker.py`
- `/path/to/nulla-hive-mind/tests/test_nulla_api_server.py`

Important rule:
- Prefer the current code state over older docs if they conflict.
- Use the docs for diagnosis, but anchor decisions to the live file contents.

## Current Reality
This is not the old fully-broken state anymore.

Already improved:
- raw leak/junk is much lower
- repeated greetings are less bot-like
- evaluative turns are better
- natural Hive task listing works better
- short follow-ups bind better
- direct utility/date/day answers work
- the raw `Autonomous research on ...` task-start classification bug has already been fixed

Remaining problem is now structural runtime contract weakness, not one obvious bug.

Do not waste time on:
- hype
- UI cosmetics
- prompt tuning
- model shopping first
- crypto/network architecture
- rewriting working Hive behavior just to simplify

## Priority Order
1. strict router precedence
2. explicit conversation state machine
3. hard response sanitization / typed response contract
4. task-progress / task-start chat contract cleanup
5. regression coverage expansion
6. only after that, optional model-side notes

## Required Outcomes

### A. Enforce one strict routing order in `run_once()`
The effective order must be:
1. utility hard overrides
2. active interaction-state follow-up
3. explicit Hive intents
4. UI/system utility intents
5. emotional/evaluative conversation
6. smalltalk / presence
7. Hive-adjacent safe recovery
8. generic tool/model path
9. final safe fallback

Do not leave routing precedence scattered across helpers in a way that makes behavior accidental.

### B. Formalize a real interaction state machine
Need explicit modes like:
- `idle`
- `smalltalk`
- `utility`
- `hive_nudge_shown`
- `hive_task_list_shown`
- `hive_task_selection_pending`
- `hive_task_active`
- `hive_task_status_pending`
- `research_followup`
- `generic_conversation`
- `error_recovery`

Need:
- exact transition logic
- exact clear/expiry rules
- conflict rules so stale Hive context does not pollute utility/general turns

Required behavior:
- if exactly one pending Hive task exists and user says `yes` / `ok` / `do it`, bind automatically
- if multiple pending tasks exist, re-list or clarify
- do not silently default-pick when ambiguous
- never emit fake task scaffolding
- never emit generic research boilerplate as pretend task state

### C. Harden the response-class / sanitization contract
All final user-visible answers must be rendered from typed turn results, not raw branch strings.

Need a strong `ResponseClass` / `ChatTurnResult` contract that cleanly separates:
- user-visible chat
- orchestration/process narration
- trace/debug/runtime truth

Normal user chat must never expose:
- `invalid tool payload`
- `missing_intent`
- `I won't fake it`
- stack traces
- raw branch labels
- raw runtime/operator narration
- fake planner/research scaffolding like:
  - `define question`
  - `search trusted sources`
  - `compare findings`
  - `summarize result`

### D. Tighten footer policy by user value, not branch accident
Footer policy is already better than before. Do not regress it.

Implement a stronger contract for:
- when footer adds real next-step value
- when footer is redundant noise
- when footer must be suppressed even if Hive context exists

Important:
- no footer on plain task lists if list body already contains enough next-step guidance
- no footer on generic conversation / utility / safe failure
- no footer on emotional/evaluative turns
- footer should be driven by response class + user value, not drift-prone branch behavior

### E. Improve task-start and research-progress chat contract
Goal:
- task-start / progress replies should read like assistant chat first
- trace/process detail should stay in logs/trace unless explicitly needed
- avoid runtime-shaped phrasing leaking into user chat
- preserve useful status, remove operator-ish narration

### F. Box the generic tool/model path harder
The local model is still weak when deterministic routing misses.
That means:
- generic tool/model path must be last
- it must still return typed results
- it must not control footer/workflow/chat style indirectly
- safe recovery should happen before generic planner sludge

### G. Add a bigger regression pack
Add or extend tests to cover real conversational behavior, not just narrow helper logic.

Must include prompts like:
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
- expected response class
- forbidden strings
- footer behavior
- state transitions
- ambiguity handling
- no trace/debug leakage

## Implementation Guidance
- preserve good current behavior
- do not rip out working Hive list/follow-up behavior
- do not solve this with more regex soup alone
- prefer deterministic routing, typed rendering, and explicit state
- keep changes narrow and landable
- avoid broad refactors outside `apps/nulla_agent.py` unless there is a clear blocker

## Known Hot Spots
- `/path/to/nulla-hive-mind/apps/nulla_agent.py`
  - `run_once()`
  - `_decorate_chat_response(...)`
  - `_should_attach_hive_footer(...)`
  - `_action_response_class(...)`
  - `_grounded_response_class(...)`
  - `_apply_interaction_transition(...)`
- `/path/to/nulla-hive-mind/core/hive_activity_tracker.py`
- `/path/to/nulla-hive-mind/core/tool_intent_executor.py`

## Acceptance Criteria
- routing order is visible and strict
- state machine is explicit, not best-effort
- stale Hive context expires/clears correctly
- footer policy is tied to user value
- user chat vs trace/runtime text boundary is hard
- task-progress/task-start replies are less runtime-shaped
- ambiguity is resolved by contextual policy, not weak fallback improvisation
- no forbidden debug/tool strings surface in normal chat

## Deliverables
1. brief diagnosis of the remaining structural problems in current code
2. exact code patch
3. touched-files list
4. why each change is needed
5. tests added/updated
6. exact test commands run
7. exact test results
8. residual risks / next step
9. if local tests pass, whether VM sync/restart is still needed
