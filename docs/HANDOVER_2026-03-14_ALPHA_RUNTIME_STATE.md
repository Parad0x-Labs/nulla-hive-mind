# NULLA Alpha Runtime Handover

Date: 2026-03-14
Scope: current live alpha lane state only
Authoring note: this is a handover document, not a milestone recap

## 1. Current Live Runtime State

At handover time:

- OpenClaw URL:
  - `http://127.0.0.1:18789/#token=6f67008a3350c7efb4d2d6b39b1724842fec6738a0027811`
- NULLA backend:
  - `http://127.0.0.1:11435`
- Trace Rail:
  - `http://127.0.0.1:11435/trace`

Observed runtime state:

- OpenClaw gateway is up on `127.0.0.1:18789`
- NULLA API server is running from a fresh process started at `Sat Mar 14 11:59:43 2026`
- The running NULLA process includes the latest scoped alpha bugfix changes in `apps/nulla_agent.py`
- Local model caveat:
  - runtime is using `qwen2.5:7b`
  - `qwen2.5:32b` is not installed locally

This means OpenClaw is using a fresh patched local NULLA runtime, but not the larger local model.

## 2. What Was Fixed

### 2.1 Hive malformed ask recovery

Problem:

- malformed Hive asks like `check hive mind see if any taks is up`
- and softer asks like `what's on the hive? can we do some tasks?`

were falling through to generic chat instead of the real Hive task-list path.

What was changed:

- `apps/nulla_agent.py`
  - `_recover_hive_runtime_command_input(...)`
  - recovery entry in the main runtime path

Effect:

- malformed Hive asks now retry through the canonical Hive task-list command path instead of asking dumb context questions.

### 2.2 Hive task list truth preservation

Problem:

- the watcher/public-Hive path could return a real task list
- then model-final wording could rewrite that into generic nonsense like:
  - `Security Audits`
  - `Server Maintenance`
  - `Patch Management`

What was changed:

- `apps/nulla_agent.py`
  - `_postprocess_hive_chat_surface_text(...)`
  - `_hive_task_list_mentions_real_topics(...)`

Effect:

- if the synthesized reply does not preserve real shown task titles or ids, the runtime falls back to the real task list instead of hallucinating fake categories.

### 2.3 Hive task followup selection

Problem:

- when the user selected a shown task by exact title, full shown status line, or short `#id`, NULLA sometimes repeated the task list instead of starting the chosen task path

Real bad transcript shapes:

- `Agent Commons: better human-visible watcher and task-flow UX`
- `[researching] Agent Commons: better human-visible watcher and task-flow UX (#7d33994f). -- full research on this pls!`
- `#a951bf9d`
- `#a951bf9d. lets do this in full!`

What was changed:

- `apps/nulla_agent.py`
  - selection gate using `shown_titles`
  - `_looks_like_hive_research_followup(...)`

Effect:

- the selected task now resolves to the real chosen topic id and enters the `research_topic_from_signal(...)` path instead of relisting.

Important precision:

- this is proven at the runtime selection layer
- it proves NULLA starts the task path with the correct selected topic id
- it does not yet prove full end-to-end autonomous completion of an arbitrary Hive task

### 2.4 Time utility binding

Problem:

- `what time is now in Vilnius?` leaked placeholder output like `[time]`

What was changed:

- `apps/nulla_agent.py`
  - `_extract_utility_timezone(...)`
  - `_utility_now_for_timezone(...)`
  - utility time fast-path binding

Effect:

- covered Vilnius time asks now return a real bound value instead of placeholder text.

### 2.5 Planner leakage cleanup

Problem:

- raw internal planner sludge was still reaching the user, including:
  - `review problem`
  - `choose safe next step`
  - `validate result`

What was changed:

- `apps/nulla_agent.py`
  - `_strip_planner_leakage(...)`
  - `_contains_generic_planner_scaffold(...)`

Effect:

- those naked planner scaffold lines are stripped from covered user-facing outputs.

### 2.6 Malformed followup recovery for Vilnius time

Problem:

- after a failed time exchange, malformed followups around Vilnius could collapse into generic brochure text

What was changed:

- same utility binding zone in `apps/nulla_agent.py`

Effect:

- malformed Vilnius time followups now recover back into the utility lane on the covered path.

## 3. Exact Files Touched For These Alpha Bugfixes

Runtime:

- `apps/nulla_agent.py`

Regression tests:

- `tests/test_nulla_hive_task_flow.py`
- `tests/test_nulla_runtime_contracts.py`

These are the files directly relevant to the current scoped bugfix handover.

## 4. Exact Code Areas That Matter

The most important branches in `apps/nulla_agent.py` are:

- Hive recovery entry
- Hive task-list truth-preserving postprocess
- Hive shown-title / short-id followup matcher
- utility timezone extraction and time binding
- planner leakage cleanup

At handover, these line anchors were the relevant ones:

- around `264`
- around `1819`
- around `1842`
- around `2374`
- around `2420`
- around `2429`
- around `5080`
- around `5112`
- around `5131`
- around `5330`
- around `6091`
- around `6803`

The exact line numbers can drift, but those are the current hotspots.

## 5. Regression Tests Added Or Relied On

### Hive task flow

`tests/test_nulla_hive_task_flow.py`

Covers:

- malformed Hive ask recovery
- `what's on the hive? can we do some tasks?` recovery
- selecting by exact shown title
- selecting by short `#id`
- selecting by noisy full shown status line plus followup phrase
- selecting by noisy short `#id` plus followup phrase

### Runtime contracts

`tests/test_nulla_runtime_contracts.py`

Covers:

- `what time is now in Vilnius?` returning a real bound value
- malformed Vilnius time followup recovery
- stripping naked planner scaffold lines

## 6. Current Verified Test State

At handover time:

Focused Hive selection and recovery slice:

- passed

Focused utility/planner slice:

- passed

Broader scoped runtime slice:

- `34 passed, 1 failed`

The one current known failing test is:

- `tests/test_nulla_runtime_contracts.py::test_unsupported_builder_request_reports_gap_honestly_instead_of_writing_a_brief`

Current failure shape:

- expected response class: `utility_answer`
- actual response class: `research_progress`

This is not part of the four fixed transcript failures. It is still broken.

## 7. What Is Still Broken

This section is the hard pill. These are the known remaining problems that matter.

### 7.1 Unsupported builder-gap classification is still wrong

Status:

- still broken

Symptom:

- unsupported builder asks like:
  - `build a web scraper service in this workspace and write the files`

still classify as active research/build progress instead of an honest unsupported-capability answer.

Where:

- `apps/nulla_agent.py`
  - builder admission logic around the bounded builder gate

Why:

- the builder entry filter is still broad enough to accept generic `service` + `write the files` requests as real bounded work
- honest gap reporting loses before the builder lane grabs the turn

What needs to happen:

- narrow builder admission to truly supported bounded flows first
- force generic unsupported build requests into the existing capability-gap renderer

### 7.2 Time followup chaining is still weak outside the patched Vilnius case

Status:

- still partially broken

Seen failure shapes:

- `ok what is time in Roma now?`
- `in London?`

Why:

- city alias handling is narrow
- short followup carryover from the previous time question is weak

What needs to happen:

- add city aliases like `Roma -> Europe/Rome`
- add one-turn followup carryover for compact location-only followups after a successful time utility answer

### 7.3 Current-events escalation is still weak

Status:

- still partially broken

Seen failure shape:

- `what the fuck is happening in Iran?`

Why:

- explicit current-events asks do not always force a fresh lookup path strongly enough
- some still slip into generic model summary mode

What needs to happen:

- force browse/escalation for explicit current-events framing on ordinary chat surfaces
- degrade honestly if fresh lookup fails

### 7.4 Named-entity research recovery is still weak

Status:

- still broken in edge cases

Seen failure shapes:

- `There is a guy named Toly or something, he is known in Solana community. who is he?`
- `find it, check Twitter and Google?`
- `check Tolly ?`

Why:

- entity disambiguation is weak before web escalation
- name-variation retries are not strong enough

What needs to happen:

- fuzzy retry path for likely person-name variants
- stronger direct escalation when the user explicitly asks to check Twitter/Google/web

### 7.5 Simple factual sanity is still too weak

Status:

- still broken in some cases

Seen failure shape:

- `what is capital of Riga?`

Why:

- trivial fact responses still rely too much on raw model output

What needs to happen:

- add cheap sanity rails for obvious geography/capital fact questions
- or force lookup for likely fact questions if certainty is low

### 7.6 Full Hive task completion flow is not proven

Status:

- not proven

What is proven:

- NULLA can recover to the Hive task list
- NULLA can preserve the real shown task list
- NULLA can select the chosen shown task and start the task path with the correct topic id

What is not proven:

- complete task execution from OpenClaw through research, result submission, and approval/finish on the live Hive path

This matters because selection is fixed, but full autonomous task completion is still a separate proof problem.

## 8. Why The Repo Feels Messy

The worktree is heavily dirty.

That is expected right now because the repo contains accepted work across:

- runtime AI-first conversion
- continuity
- Hive truth
- safety split
- adaptive research
- workflow planning
- builder loop
- alpha hardening
- workstation UI work

This handover is not claiming the tree is clean. It is not.

For this bugfix lane, the only files that matter directly are:

- `apps/nulla_agent.py`
- `tests/test_nulla_hive_task_flow.py`
- `tests/test_nulla_runtime_contracts.py`

## 9. What To Do Next

This is the recommended order if the goal is to keep killing user-visible dumb behavior before any expansion.

### Step 1

Fix unsupported builder-gap classification.

Reason:

- it is the one currently proven failing regression in the broader scoped slice
- it directly affects honesty on alpha-critical asks

Minimum proof:

- the currently failing builder-gap test passes
- response class is `utility_answer`
- response says there is no real bounded builder path for that request

### Step 2

Fix time followup carryover and city aliases.

Reason:

- the Rome/London exchange still looks stupid in live chat

Minimum proof:

- `what time is now in Roma?` binds to Rome
- `in London?` recovers from the prior time turn and returns London time cleanly

### Step 3

Fix live-info escalation for blunt current-events asks.

Reason:

- current-events questions still slide into generic filler

Minimum proof:

- explicit current-events asks trigger fresh-lookup behavior or honest degradation

### Step 4

Fix fuzzy named-entity research recovery.

Reason:

- user explicitly asked to search the web and NULLA still acted dumb

Minimum proof:

- `Toly` / `Tolly` style followups trigger better search retries and stop bluffing

### Step 5

Fix simple fact sanity on obvious low-ambiguity questions.

Reason:

- wrong simple facts destroy trust fast

Minimum proof:

- obvious capital/geography sanity cases stop hallucinating

### Step 6

Only after the above, prove a full live Hive task execution path end-to-end.

Reason:

- task selection is no longer the blocker
- execution truth and completion truth are the next real proof problem

## 10. Commands Worth Reusing

Focused Hive bugfix slice:

```bash
pytest -q tests/test_nulla_hive_task_flow.py -k 'status_line_and_full_research_phrase or short_id_with_full_phrase or typo_recovery or whats_on_the_hive'
```

Focused utility/planner slice:

```bash
pytest -q tests/test_nulla_runtime_contracts.py -k 'utility_time_in_vilnius or malformed_vilnius_time_followup_recovers_to_bound_utility_answer or sanitization_contract_strips_generic_planner_scaffold'
```

Known still-failing builder-gap test:

```bash
pytest -q tests/test_nulla_runtime_contracts.py -k 'unsupported_builder_request_reports_gap_honestly_instead_of_writing_a_brief'
```

Current NULLA server launch pattern:

```bash
env NULLA_OLLAMA_MODEL=qwen2.5:7b python3 -m apps.nulla_api_server --bind 127.0.0.1 --port 11435
```

## 11. Bottom Line

The scoped alpha transcript failures around:

- Hive malformed task-list asks
- Hive shown-task selection
- Vilnius time binding
- planner leak sludge
- malformed Vilnius followup recovery

are fixed and running on the live local NULLA process.

The system is still not clean.

The biggest currently proven remaining alpha bug in the nearby slice is:

- unsupported builder asks still misclassify as `research_progress` instead of honest gap reporting

And there are still known weak spots in:

- time followup chaining
- current-events escalation
- fuzzy named-entity research
- trivial fact sanity
- full end-to-end Hive task completion proof
