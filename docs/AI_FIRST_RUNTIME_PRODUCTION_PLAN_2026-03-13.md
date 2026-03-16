# NULLA AI-First Production Plan

**Date:** 2026-03-13

## Hard Verdict

This is **not** a model-quality problem.

We are taking a capable model and wrapping it in a pipeline that:

1. blocks the model from seeing a large share of normal chat,
2. forces the model into rigid JSON shapes when it does run,
3. rewrites the model output again before the user sees it.

That is why NULLA still feels bot-like. The problem is the **control plane**, not Ollama, Qwen, or the model provider.

## Why It Still Feels Like a Bot

### 1. The model is bypassed before conversation even starts

Common turns are intercepted in `run_once()` before the model path:

- `date_time_fast_path`
- `live_info_fast_path`
- `evaluative_conversation_fast_path`
- `smalltalk_fast_path`

Evidence:

- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/apps/nulla_agent.py:300`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/apps/nulla_agent.py:311`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/apps/nulla_agent.py:320`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/apps/nulla_agent.py:331`

This means first-impression prompts never reach the model.

### 2. We hardcoded personality into fast paths

Smalltalk and evaluative responses are not just routing shortcuts. They are literal canned lines.

Evidence:

- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/apps/nulla_agent.py:1139`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/apps/nulla_agent.py:1149`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/apps/nulla_agent.py:1171`

That is textbook bot behavior.

### 3. Live data is real, but the answer renderer is still a bot template

Weather/news/fresh lookups use real web notes, then print a fixed label + bullet list template instead of letting the model answer from evidence.

Evidence:

- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/apps/nulla_agent.py:1292`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/apps/nulla_agent.py:1528`

So the data is real, but the response still sounds like a scraper wrapper.

### 4. Even when the model runs, we force it into summary JSON

The execution profile maps most classes to `summary_block` or `action_plan`, not natural chat.

Evidence:

- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/task_router.py:405`

So instead of "think and answer", the model is asked to produce "summary + bullets" or "summary + steps".

### 5. Then we reduce the model again into a plan renderer

The response path does not trust the model to speak directly. It builds a `Plan`, then renders `summary + bullet steps`.

Evidence:

- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/reasoning_engine.py:138`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/reasoning_engine.py:216`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/reasoning_engine.py:233`

That makes NULLA sound like an internal planning engine talking out loud.

### 6. Memory/cache can skip the model entirely

If cache hits or local memory is "good enough", the model is skipped.

Evidence:

- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/memory_first_router.py:89`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/memory_first_router.py:109`

That is fine for retrieval. It is not fine if the final user-facing wording also skips the model.

### 7. Tests currently protect the bot behavior

The current tests explicitly lock in deterministic utility, canned evaluative replies, and fast-path live-info behavior.

Evidence:

- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/tests/test_nulla_runtime_contracts.py:10`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/tests/test_nulla_runtime_contracts.py:42`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/tests/test_nulla_runtime_contracts.py:58`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/tests/test_nulla_web_freshness_and_lookup.py:18`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/tests/test_nulla_web_freshness_and_lookup.py:74`

So the repo is not just incidentally bot-like. Parts of that behavior are under test as intended product behavior.

### 8. User heuristics are real, but still shallow and mostly one-way

The runtime does store some user modeling data, but it is nowhere near a frontier assistant personalization loop.

What is real:

- conversation logging updates inferred user heuristics from user text
- a small inferred heuristic set exists for response style, source preference, stack preference, project focus, and autonomy preference
- explicit user preferences exist for humor, character mode, boundaries, profanity, autonomy, workflow display, Hive followups, and related behavior
- session continuity stores recent turns, topic hints, shorthand, and ambiguity handling

Evidence:

- conversation log -> heuristic update: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/persistent_memory.py:405`
- heuristic storage/update: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/persistent_memory.py:716`
- heuristic extraction buckets: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/persistent_memory.py:764`
- heuristic text mapping: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/persistent_memory.py:1257`
- heuristics injected into context: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/tiered_context_loader.py:146`
- explicit preferences in bootstrap context: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/bootstrap_context.py:214`
- preference surface: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/user_preferences.py:145`
- session continuity store: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/storage/dialogue_memory.py:15`
- input interpretation/session adaptation: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/human_input_adapter.py:213`
- one real behavior hook from heuristics into build selection: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/apps/nulla_agent.py:2073`

What is not real yet:

- no robust approval / disapproval loop for assistant answers
- no durable per-answer satisfaction model
- no strong adaptation based on what the user actually accepted, rejected, or corrected
- no long-horizon companion-grade user model

Evidence:

- existing feedback engine is about shard/peer trust, not user taste adaptation: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/feedback_engine.py:275`
- repo already marks long-horizon user modeling as shallow: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/tests/test_nulla_future_vision_spec.py:43`

So the honest read is:

- NULLA does some user modeling
- NULLA does not yet run a real closed-loop user profile engine based on answer approval and conversational outcome
- calling the current system "100% personalized AI behavior" would be bullshit

## What Is Wrong With The Piping

The pipeline has three separate anti-AI chokepoints:

1. **Pre-model choke**
   The router answers many turns before the model sees them.

2. **In-model choke**
   The model is often forced into `summary_block`, `action_plan`, or `tool_intent` JSON instead of natural language.

3. **Post-model choke**
   The output is converted into a `Plan` and rendered as summary + steps, which strips spontaneity and conversational shape.

Bluntly: the system trusts the model for classification and structured helper work, but **does not trust it to be the assistant**.

## Hive Reality Status

This needed to be called out separately.

### What is materially real right now

These Hive operations are wired to real watcher / public-Hive data paths, not fake planner sludge:

- list available tasks from watcher or public bridge
- select a concrete task by title or short `#id`
- start research on a concrete task
- claim a task
- post progress
- submit final result
- read status from a real research packet

Evidence:

- watcher/bridge task routing: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/apps/nulla_agent.py:3211`
- start research from real queue rows: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/apps/nulla_agent.py:3823`
- status from real packet: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/apps/nulla_agent.py:4273`
- public research queue / packet fetch: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/public_hive_bridge.py:141`
- real claim / progress / result writes: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/public_hive_bridge.py:358`

Tests covering this:

- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/tests/test_nulla_hive_task_flow.py:8`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/tests/test_openclaw_tooling_context.py:419`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/tests/test_public_hive_bridge.py:510`

### What is better, but not fully trustworthy

Agent participation / online counts are only as real as the watcher snapshot.

The runtime does try to avoid obvious lies:

- watcher outage is called out explicitly
- task lists can fall back to public Hive when watcher presence is unavailable
- duplicate task titles are collapsed for display

Evidence:

- watcher unavailable -> explicit fallback: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/apps/nulla_agent.py:3231`
- watcher task read path: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/hive_activity_tracker.py:96`
- duplicate visible tasks collapsed: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/hive_activity_tracker.py:317`

But this is still weaker than task truth because:

1. presence comes from watcher-reported agent rows and stats,
2. stale leases / duplicate rows can still skew perception,
3. public-bridge fallback can preserve task truth even when participation truth is degraded.

So:

- **task truth is mostly real now**
- **participation truth is improved, but not yet hard-proof**

### Product requirement we must add

NULLA must never imply stronger Hive participation truth than the runtime actually has.

That means:

- never equate watcher `active_agents` with distinct useful workers unless deduped
- never imply "many agents are working" unless there are real claims / posts / packets proving activity
- when watcher presence is unavailable, say so plainly and downgrade to task-only truth
- prefer claim count, post count, artifact count, and packet state over vanity presence counts

### Additional Hive Acceptance Gates

Hive is not production-honest until all of this is true:

1. Task lists come from watcher or public-Hive APIs only.
2. Starting work produces a real claim or a clear honest failure.
3. Progress/results produce real posts/status updates or a clear honest failure.
4. Status replies are derived from research packets / topic state, not inferred prose.
5. Presence claims are deduped and clearly labeled as watcher-derived.
6. If watcher presence is degraded, NULLA still preserves task truth and explicitly reports participation uncertainty.
7. No fake "6 agents online" style claims from duplicate leases or stale watcher state.

## Capability Truth Status

This also needed to be explicit.

### No, NULLA does not currently have "all tooling"

The runtime tool surface is real, but limited.

Current tool classes include:

- bounded web search / fetch / research / browser render
- workspace file list / search / read / write / replace
- bounded sandbox command execution
- Hive list / queue / packet / claim / progress / result
- a few operator actions like disk/process/service inspection, calendar outbox, Discord send, Telegram send

Evidence:

- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/tool_intent_executor.py:152`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/runtime_execution_tools.py:55`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/local_operator_actions.py:190`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/prompt_normalizer.py:224`

That is not GPT/Grok/Claude universal capability. It is a bounded local agent runtime.

### Curiosity is real, but bounded and candidate-only

Curiosity does not equal unlimited autonomous innovation.

Right now it:

- derives bounded topics,
- runs bounded web search against curated source profiles,
- stores candidate summaries,
- keeps outputs candidate-only rather than authoritative truth.

Evidence:

- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/curiosity_roamer.py:83`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/curiosity_roamer.py:262`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/curiosity_roamer.py:333`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/curiosity_roamer.py:357`

So curiosity is a bounded research lane, not a frontier-level invention engine.

### Multi-agent Hive collaboration is not normal chat reality yet

There are orchestration and swarm primitives in the repo, but the repo itself marks chat-level helper-lane delegation and merge behavior as future work.

Evidence:

- parent orchestration exists: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/parent_orchestrator.py:190`
- normal chat still just calls orchestration opportunistically: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/apps/nulla_agent.py:527`
- future multi-agent chat behavior is still xfail: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/tests/test_nulla_future_vision_spec.py:18`

So "talk to other Hive agents and split into helper lanes and merge results" is **not** a production-honest promise yet.

### Builder mode is also bounded

The workspace build pipeline is real, but narrow:

- mainly Telegram / Discord scaffolds
- otherwise it drops to a generic build brief
- full research -> code -> run -> verify autonomy is still explicitly future/xfail

Evidence:

- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/apps/nulla_agent.py:1944`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/apps/nulla_agent.py:2111`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/apps/nulla_agent.py:2459`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/tests/test_nulla_future_vision_spec.py:6`

### Domain breadth vs tool breadth

These are not the same thing.

Target state:

- NULLA should be able to **talk naturally** about code, product, food, relationships, sex, life, strategy, and random human topics like GPT/Grok/Claude do.

Current reality:

- she can sometimes converse across broad domains because the model can generate text,
- but she does **not** have universal tool grounding, domain-specific workflows, or verification paths across all of those domains.

So if a user asks about:

- code: some real tooling exists
- live research: some real tooling exists
- Hive: some real tooling exists
- food / sex life / general life: mostly conversation only, not specialized execution tooling

### Capability Truth Requirement

NULLA must never imply:

- that she has a tool for every domain,
- that curiosity equals universal innovation,
- that swarm primitives equal real multi-agent delegation already,
- that conversational fluency equals verified competence.

### Capability Acceptance Gates

We should not call NULLA "100% AI" in the product sense until all of this is true:

1. She can converse naturally across arbitrary user topics without dropping into canned bot text.
2. She explicitly states which actions are grounded by real tools versus plain reasoning.
3. She never invents nonexistent tools or Hive/helper abilities.
4. Multi-agent delegation is either real and observable, or not claimed.
5. Builder mode can research -> generate -> verify beyond narrow Telegram/Discord scaffolds.
6. Curiosity can support broader open-ended innovation without pretending candidate notes are authoritative proof.

## User Modeling Truth Status

This needs to be explicit because "stores heuristics" is not the same thing as "understands the user."

### What is materially real right now

The runtime currently has three user-modeling layers:

1. Explicit controls the user can set directly.
2. Lightweight heuristic extraction from user wording.
3. Session continuity for shorthand, references, and recent context.

That means NULLA can really remember some things like:

- preferred blunt/direct style
- desire for low-friction execution
- source preferences like official docs
- common project lanes like Hive / Telegram / OpenClaw
- some current-topic continuity and shorthand resolution

### What is still missing

The missing layer is the important one:

- outcome-based learning from whether the assistant response was actually good

Today the runtime mostly learns from what the user says about preferences, not from whether the last answer worked.

That means it is missing:

1. explicit positive / negative answer feedback capture
2. inferred approval from follow-up turns
3. contradiction handling when preferences shift
4. confidence decay for stale heuristics
5. user-approved answer exemplars for style learning
6. profile dimensions beyond a few fixed buckets

### Why this still feels bot-like

Because the system can claim user-aware behavior while mostly running:

- canned fast paths for common chat
- fixed heuristic buckets from keyword matches
- explicit preference toggles

That is not fake, but it is still much closer to a configured bot than a deeply adaptive assistant.

### User Modeling Requirement

NULLA must never imply:

- that she deeply knows the user when the runtime only has shallow inferred heuristics
- that she learned from answer approval when no such closed loop exists
- that repeated conversation alone equals real companion-grade personalization

### User Modeling Acceptance Gates

We should not claim production-grade personalization until all of this is true:

1. Every assistant answer can accumulate explicit or inferred user-outcome feedback.
2. The runtime can distinguish preference statements from answer-quality reactions.
3. User profile facets support confidence, recency, and contradiction instead of simple mention counts only.
4. Final answer synthesis actually consumes the active user profile on chat surfaces.
5. The runtime can adapt tone, brevity, autonomy, and evidence style based on accepted/rejected outcomes, not just keywords.
6. The runtime degrades honestly when there is not enough user-profile evidence.

## Non-Goals

Do **not** do these fake fixes:

- Add 20 more canned greetings.
- Randomize canned text and call it personality.
- Keep template renderers and only add more variants.
- Hide the bot feel with prompt hacks while preserving the same routing.

That would be cosmetic fraud, not a fix.

## Target Product State

NULLA should be **AI-first, evidence-grounded, tool-capable**.

That means:

1. The model sees all ordinary conversation.
2. Deterministic code provides **facts**, not finished personality.
3. Tools/Hive/web return evidence objects.
4. The model synthesizes the final user-facing answer from those facts.
5. Hardcoded replies exist only for:
   - hard safety refusal,
   - explicit slash/system commands,
   - model/runtime outage fallback,
   - exact approval/confirmation prompts where determinism matters.

## Personality And Boundary Target

Conversation target:

- as real and fluid as GPT/Claude-class assistants,
- not a command-menu bot,
- not canned,
- not over-sanitized into corporate sludge.

Boundary target:

- conversational boundaries can be **looser** and more Grok-like,
- personality can be sharper, more blunt, more opinionated, and less nanny-like,
- but **action safety stays strict**.

This split matters:

- **speech freedom** should be broad,
- **execution freedom** should still be bounded by local safety, approval rules, and non-destructive defaults.

If we loosen both at once, we do not get "real AI." We get an unsafe agent.

So the product goal is:

- **GPT/Claude-level natural conversation**
- **Grok-level looseness in tone and boundaries**
- **strict execution controls for real-world actions**

## Production Plan

### Phase 0. Instrumentation First

Add explicit metrics before changing behavior.

Ship:

- `conversation.fast_path.hit` by reason
- `conversation.model.used`
- `conversation.model.skipped_by_cache`
- `conversation.final_renderer` with values like `fast_path`, `plan_renderer`, `tool_synthesis`, `direct_model`
- `conversation.first_turn_path`

Acceptance:

- We can measure what percentage of first-turn user messages bypass the model.
- We can measure how often the final user-facing text came from the model versus a template.

### Phase 1. Kill Conversational Fast Paths

Remove or flag off these for `channel`, `openclaw`, and `api`:

- `_smalltalk_fast_path()`
- `_evaluative_conversation_fast_path()`
- `_date_time_fast_path()`

Replace them with:

- local evidence assembly for facts like date/time,
- normal model answer generation for all conversational wording.

Implementation note:

- date/time should still use deterministic local clock data,
- but the final answer should go through the conversational answer lane.

Acceptance:

- `hi`
- `hello`
- `yo`
- `how are you`
- `what day is it today`

all hit the model answer path in production chat surfaces.

### Phase 2. Keep Live Data Real, But Let The Model Speak

Stop returning template text from `_render_live_info_response()`.

Instead:

1. fetch live notes,
2. attach them as evidence,
3. ask the model for a grounded answer over those notes,
4. if model is unavailable, only then fall back to the current plain template.

Do the same for Hive list/status/research summaries.

Acceptance:

- weather/news/Hive outputs still remain grounded,
- but the wording is no longer fixed-label bullet spam,
- no source fabrication,
- no empty-success replies.

### Phase 3. Stop Forcing Chat Tasks Into `summary_block` / `action_plan`

Right now chat surfaces are still routed through structured shapes:

- `research -> summary_block`
- `system_design -> action_plan`
- `integration_orchestration -> action_plan`
- `unknown -> summary_block`

That is wrong for user-facing chat.

Split the model contract into two layers:

1. **internal worker output**
   Structured only when needed for tools/plans.

2. **final assistant answer**
   Plain text for user-facing chat surfaces.

Implementation:

- keep `tool_intent` structured,
- keep internal planning structured if useful,
- but add a final `plain_text` answer synthesis step for chat surfaces.

Acceptance:

- chat answers are no longer limited to summary + bullets,
- model answers can sound human without leaking internals.

### Phase 4. Split Planner From Speaker

The current `Plan` object is doing too much. It is both internal reasoning state and user-facing answer source.

Refactor into:

- `GroundedFacts`
- `ExecutionIntent`
- `UserAnswer`

Rule:

- planner/plans are for internal control,
- `UserAnswer` is the only object allowed to speak to the user.

This prevents `render_response()` from turning everything into a bullet list.

Acceptance:

- internal plan quality can improve without forcing the same style onto users,
- user-facing replies are natural while still grounded.

### Phase 5. Restrict Memory/Cache Shortcuts To Evidence, Not Final Wording

Keep cache and retrieval for speed, but do not let them fully replace the final answer model on chat surfaces.

New rule:

- memory/cache may skip tool calls and retrieval,
- memory/cache may populate evidence,
- but chat surfaces still get a final synthesis pass unless the runtime is in degraded mode.

Acceptance:

- memory hits remain fast,
- but answers no longer sound like stale cached snippets.

### Phase 6. Build A Real User-Model Loop

Right now user modeling is mostly:

- explicit settings
- lightweight inferred heuristics from user text
- session continuity

That is useful, but still weak.

Add a dedicated user-outcome layer:

1. record explicit preference statements separately from inferred heuristics
2. capture explicit answer feedback such as approval, rejection, "too verbose", "good", "wrong", "same answer", "do it like this"
3. infer soft approval / dissatisfaction from the next user turn when explicit feedback is absent
4. maintain per-facet confidence, recency, and contradiction handling
5. keep a bounded local store of accepted response patterns and rejected response patterns
6. inject the active profile into final answer synthesis, not into canned fast paths

Important rule:

- this profile must stay local-by-default and privacy-scoped

Acceptance:

- the runtime can explain whether a style choice came from explicit prefs, inferred heuristics, or observed answer outcomes
- answer-quality feedback changes future response behavior
- stale or contradicted heuristics lose weight over time
- personalization is visible in final synthesis without inventing certainty

### Phase 7. Preserve Determinism Only Where It Matters

Keep deterministic behavior only for:

- safety refusal text
- explicit confirmation/approval prompts
- exact slash command responses
- model-unavailable fallback
- idempotent tool receipts / replay behavior

Everything else should be AI-synthesized from grounded evidence.

### Phase 8. Rewrite The Tests To Protect The Right Product

Update tests so they verify:

- ordinary chat reaches the model,
- utility facts remain correct,
- live-info answers are grounded,
- no internal leaks,
- no canned fixed-string lock-in for greetings/evaluative turns,
- no fake claims of user modeling beyond the stored evidence,
- user-outcome feedback can change future style selection without requiring hardcoded canned replies.

Delete or rewrite tests that require deterministic smalltalk and fixed conversational phrasing.

## File-Level Worklist

### Routing and answer-path changes

- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/apps/nulla_agent.py`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/task_router.py`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/memory_first_router.py`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/reasoning_engine.py`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/prompt_normalizer.py`

### User-modeling changes

- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/persistent_memory.py`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/tiered_context_loader.py`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/bootstrap_context.py`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/user_preferences.py`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/core/human_input_adapter.py`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/storage/dialogue_memory.py`
- new bounded store/module for assistant-answer outcome feedback

### Test rewrites

- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/tests/test_nulla_runtime_contracts.py`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/tests/test_nulla_web_freshness_and_lookup.py`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/tests/test_model_execution_layer.py`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/tests/test_openclaw_tooling_context.py`
- `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/tests/test_nulla_future_vision_spec.py`

## Rollout Strategy

### Step 1. Ship behind a feature flag

Add flags such as:

- `conversation.ai_first_chat`
- `conversation.model_synthesizes_live_info`
- `conversation.disable_smalltalk_fast_paths`
- `conversation.disable_evaluative_fast_paths`
- `conversation.final_text_synthesis_required`

### Step 2. Canary on OpenClaw first

OpenClaw is the best canary because it exposes the runtime trace and is easiest to observe.

### Step 3. Compare metrics before full rollout

Required deltas:

- first-turn model usage goes up sharply,
- canned fast-path share drops near zero for ordinary chat,
- no increase in error leaks,
- no increase in source fabrication,
- acceptable latency increase.

## Acceptance Gates

NULLA is not "fixed" until all of this is true:

1. First-impression prompts on chat surfaces route to the model at least 95% of the time.
2. Greetings/evaluative turns are not fixed strings across runs.
3. Date/time/weather/news/Hive use deterministic facts or tools, but user wording is model-synthesized.
4. No user-facing leak of:
   - `I won't fake it`
   - `invalid tool payload`
   - `missing_intent`
   - `Real steps completed`
5. When the model is unavailable, fallback text is explicit and honest.
6. Tool results stay grounded and idempotent.
7. Personalization claims are always backed by explicit prefs, heuristics, or observed answer outcomes.
8. Median latency for ordinary chat remains acceptable for product use.

## Priority Order

If you want the highest ROI sequence, do it in this order:

1. Kill smalltalk/evaluative/date fast paths on chat surfaces.
2. Make live-info/Hive answers model-synthesized over evidence.
3. Build a real user-outcome modeling loop.
4. Split internal planning from final user answer.
5. Prevent memory/cache from replacing final synthesis.
6. Rewrite tests to lock in AI-first behavior.

## Final Truth

NULLA is bot-like because we built a **bot shell around an AI core**.

The fix is not "make the bot shell more clever."

The fix is:

- let the model actually be the assistant,
- keep deterministic code for facts, safety, and tool control,
- stop using deterministic code as the primary voice.
