# NULLA AI-First Repo Audit

Date: 2026-03-13

Scope:
- Repo audit only.
- No live external-network validation was performed for this pass.
- Hive write/read claims below are based on code paths and tests, not a live cluster run.

## 1. System Prompt Choke Points

- Refs:
  - `apps/nulla_agent.py:468-675`
  - `core/tiered_context_loader.py:408-520`
  - `core/bootstrap_context.py:33-287`
  - `core/prompt_normalizer.py:25-173`
  - `core/prompt_normalizer.py:176-307`
  - `core/internal_message_schema.py:17-21`
- Current behavior:
  - Normal conversation path is `run_once()` -> `TieredContextLoader.load()` -> `MemoryFirstRouter.resolve()` -> `normalize_prompt()` -> provider call -> `build_plan()` -> `render_response()`.
  - Persona is injected in bootstrap context at `core/bootstrap_context.py:49-63`.
  - Task/risk/safety are injected at `core/bootstrap_context.py:79-108`.
  - Self-knowledge and doctrine docs are injected at `core/bootstrap_context.py:111-142`.
  - Owner identity, runtime memory, session memory policy, and user preferences are injected at `core/bootstrap_context.py:144-227`.
  - Recent dialogue and shorthand are injected at `core/bootstrap_context.py:245-287`.
  - Relevant memory, heuristics, session summaries, swarm context, and prior final responses are added in `core/tiered_context_loader.py:452-472`.
  - The assembled context is appended directly inside the `system` prompt in `core/prompt_normalizer.py:129-155`.
  - Tool notes and approval posture are injected in `_tooling_guidance()` at `core/prompt_normalizer.py:224-273`.
  - Formatting and JSON contracts are injected in `_chat_output_guidance()` at `core/prompt_normalizer.py:276-285`.
  - `context` messages are converted to `assistant` role before hitting the provider in `core/internal_message_schema.py:17-21`.
  - Exact prompt stack by path:
    - Greeting: no prompt stack at all. `run_once()` exits before inference through `apps/nulla_agent.py:300-345` and `_smalltalk_fast_path()` at `apps/nulla_agent.py:1128-1169`.
    - Open-ended question: `apps/nulla_agent.py:468-675` -> `core/tiered_context_loader.py:408-520` -> `core/prompt_normalizer.py:106-173`.
    - Live-info answer: usually no prompt stack. It exits via `_maybe_handle_live_info_fast_path()` at `apps/nulla_agent.py:1292-1348` and `_render_live_info_response()` at `apps/nulla_agent.py:1528-1552`. Only `fresh_lookup` with no notes falls through to the normal model path.
    - Hive/task answer: common list/status/research followups bypass the model via `apps/nulla_agent.py:3211-3258`, `apps/nulla_agent.py:3823-3973`, and `apps/nulla_agent.py:4273-4345`.
    - Builder/code request: narrow builder requests bypass the model-final-answer path via `apps/nulla_agent.py:1944-2030` and template out through `apps/nulla_agent.py:2185-2215`.
- Why it makes NULLA bot-like or less capable:
  - The system prompt is overloaded with identity, doctrine, safety, memory, tool policy, formatting contracts, and retrieved context in one block. That is not a conversation prompt. That is a control envelope.
  - Injecting retrieved memory into the `system` message makes memory sound like law, not optional context.
  - Approval language from `_tooling_guidance()` is present on ordinary chat turns, so the assistant sounds like an operations wrapper even when the user just wants a natural answer.
  - Converting `context` to `assistant` role biases the model toward parroting retrieved snippets as prior assistant voice.
  - Greeting, live-info, Hive, and builder paths often skip prompt assembly entirely, so the model never gets a chance to be the speaker.
- Required production fix:
  - Split prompt assembly into four channels:
    - `system`: stable persona, truthfulness, broad conversational policy only.
    - `developer/runtime`: hidden tool policy and execution rules only when tool use is in play.
    - `context`: retrieval evidence and memory as optional evidence, not inside `system`.
    - `history`: real rolling transcript.
  - Remove conversational fast paths for greeting/evaluative/live-info/Hive wording on chat surfaces.
  - Stop converting `context` to `assistant`; pass it as distinct evidence or summarize it into a dedicated hidden note.
  - Reserve heavy tool/approval instructions for tool-intent turns, not normal chat.
- Regression risk:
  - Fewer hard constraints will increase style variance.
  - Weak providers may hallucinate tool use if truthfulness rules are not reintroduced in a narrower execution-only layer.
  - Prompt token usage may rise if context is restructured badly.
- Tests/evals to add:
  - Prompt snapshot test for normal chat: no JSON contract, no approval language, no tool catalog in ordinary conversation.
  - Prompt snapshot test for tool-intent: tool catalog present, execution rules present, normal conversation rules still concise.
  - Integration eval: greeting on chat surface must invoke the model.
  - Integration eval: live-info answer on chat surface must either be model-synthesized over evidence or an honest failure.
- Done criteria:
  - Ordinary chat prompt contains persona + truthfulness + short style guidance only.
  - Retrieval context is no longer appended inside the `system` message.
  - Chat-surface greeting/live-info/Hive/builder answers no longer bypass prompt assembly unless the request is an explicit command.

## 2. Model Generation Settings

- Refs:
  - `core/task_router.py:405-418`
  - `core/memory_first_router.py:67-129`
  - `core/memory_first_router.py:177-245`
  - `core/prompt_normalizer.py:208-221`
  - `core/prompt_normalizer.py:276-307`
  - `adapters/openai_compatible_adapter.py:42-75`
  - `apps/nulla_api_server.py:124-160`
  - `core/model_selection_policy.py:37-87`
  - `core/model_capabilities.py:21-39`
- Current behavior:
  - Repo-default provider is auto-registered as `ollama-local:qwen2.5:7b` in `apps/nulla_api_server.py:124-160`.
  - Provider selection is dynamic by capability/trust/cost in `core/model_selection_policy.py:37-87`, but the shipped local default is the Ollama Qwen path.
  - Output modes:
    - `debugging`, `dependency_resolution`, `config`, `security_hardening`, `system_design`, `integration_orchestration` -> `action_plan` in `core/task_router.py:407-412`
    - `research`, `file_inspection`, `shell_guidance`, `unknown` -> `summary_block` in `core/task_router.py:413-416`
  - Max output tokens:
    - `plain_text`: 240
    - `summary_block`: 220
    - `json_object`: 220
    - `action_plan`: 320
    - `tool_intent`: 700
    - source: `core/prompt_normalizer.py:208-215`
  - Temperature:
    - structured modes (`json_object`, `action_plan`, `tool_intent`, `summary_block`) -> `0.1`
    - `plain_text` -> `0.2`
    - source: `core/prompt_normalizer.py:218-221`
  - JSON/schema constraints:
    - Prompt-level JSON-only contracts are enforced in `core/prompt_normalizer.py:276-285`.
    - Provider-native `response_format={"type":"json_object"}` is only set if `supports_json_mode=True` in `adapters/openai_compatible_adapter.py:54-55`.
    - Default local Ollama manifest sets `supports_json_mode=False` in `apps/nulla_api_server.py:144-152`.
  - Not configured anywhere in the repo execution payload:
    - `top_p`
    - `frequency_penalty`
    - `presence_penalty`
    - repetition penalty
    - stop sequences
    - repo proof: `adapters/openai_compatible_adapter.py:47-55`
  - Path model usage:
    - greeting/evaluative/date/live-info fast paths: no model
    - normal open-ended chat: usually Qwen via provider selection, but output mode is still usually `summary_block`
    - code/debug/system design: model, but as `action_plan`
    - builder fast path: no model final answer
    - Hive fast paths: no model final answer
- Why it makes NULLA bot-like or less capable:
  - The biggest choke is not `top_p`. It is `summary_block` / `action_plan` plus `temperature=0.1`.
  - Most ordinary chat domains route to `unknown`, and `unknown` maps to `summary_block`, so open-ended conversation is forced into a summary JSON contract.
  - Even when the provider is capable, the runtime asks it to behave like a planner/extractor, not a conversational intelligence.
  - `plain_text` exists, but the routing barely uses it on real chat topics.
- Required production fix:
  - Change default chat synthesis mode to `plain_text` for:
    - `unknown`
    - `research` answers unless the user explicitly asks for bullets/summary
    - `system_design` / `debugging` when the user asks for a direct answer instead of a plan
  - Use separate settings:
    - ordinary chat synthesis: higher temperature, e.g. `0.5-0.8`
    - tool-intent / structured extraction: keep low temperature
  - Add adaptive max tokens by request size, not a hard 220/240 cap for broad chat.
  - If structured output is needed, use a dedicated planner/model call, not the user-facing answer call.
- Regression risk:
  - Higher temperature can reduce consistency on weak local models.
  - Plain-text answers may be harder to parse if other code still expects planner JSON.
  - Some existing tests will fail because they assume `summary_block`/`action_plan`.
- Tests/evals to add:
  - Routing test: business strategy, food, relationships, creative ideation, and general chat must not map to `summary_block`.
  - Generation config snapshot test: ordinary chat uses `plain_text` and non-structured temperature.
  - Regression test: tool-intent and explicit plan requests still use structured low-temp settings.
- Done criteria:
  - Standard chat domains do not default to `summary_block`.
  - Structured low-temp settings are isolated to planner/tool calls.
  - `plain_text` is the dominant user-facing mode on chat surfaces.

## 3. Pre-Model Interception Map

- Refs:
  - `apps/nulla_agent.py:211-345`
  - `apps/nulla_agent.py:379-466`
  - `apps/nulla_agent.py:1944-2030`
  - `apps/nulla_agent.py:3211-3258`
  - `apps/nulla_agent.py:3823-3973`
  - `apps/nulla_agent.py:4273-4345`
- Current behavior:
  - Full pre-inference bypass list in `run_once()`:
    - startup sequence: `apps/nulla_agent.py:211-220`
    - preference command: `apps/nulla_agent.py:222-235`
    - Hive runtime command: `apps/nulla_agent.py:237-249`
    - Hive research followup: `apps/nulla_agent.py:251-257`
    - Hive status followup: `apps/nulla_agent.py:259-265`
    - memory command: `apps/nulla_agent.py:267-276`
    - UI slash command: `apps/nulla_agent.py:278-287`
    - credit status: `apps/nulla_agent.py:289-298`
    - date/time: `apps/nulla_agent.py:300-309`
    - live-info fast path: `apps/nulla_agent.py:311-318`
    - evaluative conversation: `apps/nulla_agent.py:320-329`
    - smalltalk/help: `apps/nulla_agent.py:331-345`
    - outbound channel posting: `apps/nulla_agent.py:379-417`
    - operator action dispatch: `apps/nulla_agent.py:419-457`
    - Hive topic create request: `apps/nulla_agent.py:459-466`
    - workspace build pipeline: `apps/nulla_agent.py:644-654`, implementation in `apps/nulla_agent.py:1944-2030`
- Why it makes NULLA bot-like or less capable:
  - First impression is dominated by deterministic handlers, not the model.
  - A large chunk of chat-adjacent behavior is decided before inference.
  - The runtime treats many normal conversational requests as command templates.
- Required production fix:
  - Keep deterministic only for explicit command/control lanes.
  - Remove deterministic wording lanes on chat surfaces.
  - Route live-info, help/capabilities, Hive list/status, and builder summaries through model synthesis over evidence.
  - Leave deterministic action execution underneath, but not deterministic user-facing narration.
- Regression risk:
  - Slower response on trivial turns.
  - More token spend.
  - If the model is down, the runtime needs a clean degraded answer instead of old canned replies.
- Tests/evals to add:
  - Route coverage test that enumerates every chat-surface fast path and fails if greeting/evaluative/live-info/Hive wording still short-circuits the model.
  - Command/control tests for the deterministic lanes that remain.
- Done criteria:
  - Chat-surface greeting/evaluative/help/live-info/Hive status/list responses no longer bypass the model.
  - Deterministic pre-model routes are limited to explicit commands and execution dispatch.

### Kill / Keep Table

| Route | Refs | Keep? | Reason |
| --- | --- | --- | --- |
| startup sequence | `apps/nulla_agent.py:211-220`, `apps/nulla_agent.py:1261-1269` | Keep | System-generated session bootstrap, not normal chat. |
| preference command | `apps/nulla_agent.py:222-235` | Keep | Explicit control command. |
| memory command | `apps/nulla_agent.py:267-276` | Keep | Explicit control command. |
| UI slash command | `apps/nulla_agent.py:278-287` | Keep | Explicit control command. |
| credit status | `apps/nulla_agent.py:289-298`, `apps/nulla_agent.py:1271-1290` | Keep deterministic data, synthesize wording | Fact lookup is deterministic; user-facing text can still be model-shaped. |
| date/time | `apps/nulla_agent.py:300-309`, `apps/nulla_agent.py:1208-1249` | Keep | Deterministic utility is fine. |
| live-info fast path | `apps/nulla_agent.py:311-318`, `apps/nulla_agent.py:1292-1348` | Kill on chat surfaces | Should become evidence collection + model synthesis. |
| evaluative conversation | `apps/nulla_agent.py:320-329`, `apps/nulla_agent.py:1171-1186` | Kill | Pure canned conversational lane. |
| smalltalk/help | `apps/nulla_agent.py:331-345`, `apps/nulla_agent.py:1128-1169` | Kill on chat surfaces | First impression bot-shell. |
| outbound post dispatch | `apps/nulla_agent.py:379-417` | Keep execution path, synthesize wording | Real action path. |
| operator action dispatch | `apps/nulla_agent.py:419-457` | Keep execution path, synthesize wording | Real action path. |
| Hive runtime command list/overview | `apps/nulla_agent.py:237-249`, `apps/nulla_agent.py:3211-3258` | Kill deterministic wording | Evidence real, wording should be model-synthesized. |
| Hive research/status followups | `apps/nulla_agent.py:251-265`, `apps/nulla_agent.py:3823-3973`, `apps/nulla_agent.py:4273-4345` | Kill deterministic wording | Evidence real, narration still template. |
| Hive topic create request | `apps/nulla_agent.py:459-466`, `apps/nulla_agent.py:3987-4120` | Keep execution path, synthesize wording | Real write path. |
| workspace build pipeline | `apps/nulla_agent.py:644-654`, `apps/nulla_agent.py:1944-2030` | Keep execution path, synthesize wording | Real file writes, but templated answer must die. |

## 4. Post-Model Flattening Map

- Refs:
  - `core/reasoning_engine.py:138-213`
  - `core/reasoning_engine.py:216-310`
  - `core/identity_manager.py:125-145`
  - `apps/nulla_agent.py:2922-3071`
  - `apps/nulla_agent.py:3423-3461`
  - `apps/nulla_agent.py:3943-3969`
  - `apps/nulla_agent.py:4323-4345`
  - `apps/nulla_agent.py:1528-1552`
  - `apps/nulla_agent.py:2185-2215`
  - `core/hive_activity_tracker.py:340-376`
- Current behavior:
  - Model output is turned into a `Plan` in `core/reasoning_engine.py:138-213`.
  - User-facing chat is then rendered from `plan.summary` and `plan.abstract_steps` in `core/reasoning_engine.py:233-310`.
  - Fallback summaries become `Here's what I'd suggest:` in `core/reasoning_engine.py:298-310`.
  - Persona wrapper can truncate output to the first paragraph when verbosity is low in `core/identity_manager.py:132-145`.
  - Workflow wrapper can prepend `Workflow:` in `apps/nulla_agent.py:2922-2941`.
  - `_shape_user_facing_text()` rewrites Hive research/task text with regex/string replacements in `apps/nulla_agent.py:2987-3023`.
  - Sanitization strips runtime errors and replaces them with generic canned fallbacks in `apps/nulla_agent.py:3047-3071`.
  - Tool-loop final synthesis is flattened into summary + bullets in `apps/nulla_agent.py:3423-3442`.
  - Tool-loop wrapper prepends `Real steps completed:` in `apps/nulla_agent.py:3444-3461`.
  - Live-info is wrapped by `_render_live_info_response()` in `apps/nulla_agent.py:1528-1552`.
  - Workspace build returns `_workspace_build_response()` template in `apps/nulla_agent.py:2185-2215`.
  - Hive followups are summary templates in `apps/nulla_agent.py:3943-3969` and `apps/nulla_agent.py:4323-4345`.
  - Hive task list and overview are tracker templates in `core/hive_activity_tracker.py:340-376`.
- Why it makes NULLA bot-like or less capable:
  - The model is not the final speaker. The planner and renderer are.
  - Even after model generation, the runtime flattens answers into summary blocks, bullets, step wrappers, and sanitized canned text.
  - This destroys natural voice, nuance, and continuity.
- Required production fix:
  - Delete `build_plan()` / `render_response()` as the default chat renderer for chat surfaces.
  - Replace with:
    - hidden planner call when needed
    - direct user-facing synthesis call over evidence/tool results
  - Remove `Real steps completed:` from user-facing chat.
  - Keep error sanitization only for explicit internal leak patterns, but preserve natural failure wording when already user-safe.
  - Move workflow summaries to developer/debug surfaces only.
  - Stop truncating to first paragraph in `render_with_persona()` for ordinary chat.
- Regression risk:
  - Loss of current planner-shaped predictability.
  - Some tests and downstream code may depend on summary/steps format.
  - Removing sanitizer shortcuts can re-expose internal garbage if the replacement synthesis path is weak.
- Tests/evals to add:
  - Chat eval that fails on planner leakage phrases: `Here's what I'd suggest`, `Real steps completed`, `Workflow:`, `summary`, `steps`.
  - Snapshot tests for tool-result answers proving final output is natural prose, not a wrapper template.
  - Hive answer evals that check truthful content but reject template-only phrasing.
- Done criteria:
  - Chat-surface answers are no longer rendered from `Plan.summary` + `abstract_steps`.
  - Tool results are synthesized into final prose without `Real steps completed:` wrappers.
  - Workflow/debug text is hidden from normal chat.

### Delete vs Keep

| Layer | Refs | Action |
| --- | --- | --- |
| `build_plan()` as chat answer source | `core/reasoning_engine.py:138-213` | Delete from chat path |
| `_render_conversational()` planner renderer | `core/reasoning_engine.py:233-310` | Delete from chat path |
| `render_with_persona()` first-paragraph truncation | `core/identity_manager.py:132-145` | Delete truncation for chat |
| `Workflow:` wrapper | `apps/nulla_agent.py:2922-2941` | Keep only for debug/dev surfaces |
| Hive task/research regex rewrites | `apps/nulla_agent.py:2987-3023` | Delete; replace with model synthesis |
| runtime preamble stripping | `apps/nulla_agent.py:3064-3071` | Keep narrowly until wrappers are removed |
| tool-loop final-message flattener | `apps/nulla_agent.py:3423-3442` | Replace with plain final synthesis |
| `Real steps completed:` wrapper | `apps/nulla_agent.py:3444-3461` | Delete from user chat |
| live-info renderer | `apps/nulla_agent.py:1528-1552` | Delete from chat path |
| workspace build response template | `apps/nulla_agent.py:2185-2215` | Replace with model synthesis |
| Hive task list/overview templates | `core/hive_activity_tracker.py:340-376` | Keep as internal formatter only, not final chat voice |

## 5. Memory and Cache Abuse

- Refs:
  - `core/memory_first_router.py:89-115`
  - `apps/nulla_agent.py:540-549`
  - `core/tiered_context_loader.py:122-169`
  - `core/tiered_context_loader.py:172-261`
  - `core/reasoning_engine.py:153-182`
  - `apps/nulla_agent.py:611-642`
  - `core/final_response_store.py:6-25`
- Current behavior:
  - The router can bypass inference entirely with an exact candidate cache hit at `core/memory_first_router.py:89-107`.
  - The router can also skip the model on a `memory_hit` at `core/memory_first_router.py:109-115`.
  - Chat surfaces partially dodge this by forcing the model in `apps/nulla_agent.py:540-549`, but the bypass still exists in the runtime and still matters on non-chat surfaces and degraded paths.
  - `TieredContextLoader` injects user heuristics, persistent memory, session summaries, shared swarm context, and prior final responses into the retrieved context in `core/tiered_context_loader.py:452-472`.
  - `build_plan()` will speak from `model_candidates`, `context_snippets`, or `web_notes` if that is what exists in `core/reasoning_engine.py:153-182`.
  - Prior finalized answers are stored and then reloaded as retrieval items through `core/final_response_store.py:6-25` and `core/tiered_context_loader.py:227-261`.
- Why it makes NULLA bot-like or less capable:
  - Cached or remembered text can become the answer instead of helping produce the answer.
  - Prior final responses can dominate voice and make NULLA sound like a recycler of old outputs.
  - If the provider is unavailable, the planner starts speaking from memory/web snippets directly, which feels like stitched retrieval, not intelligence.
- Required production fix:
  - Keep `force_model=True` for all user-facing chat surfaces.
  - Remove direct `exact_cache_hit` / `memory_hit` returns from any user-facing final answer path.
  - Treat cache/memory as evidence only:
    - drafts
    - hypotheses
    - retrieval hints
  - Remove prior finalized responses from default conversational retrieval, or demote them below user/assistant transcript and factual memory.
  - If no model is available, return an honest degraded response instead of letting retrieval speak as NULLA.
- Regression risk:
  - More provider usage.
  - Slower answers when the old cache path would have returned immediately.
  - If the provider is unstable, degraded answer rate may rise before failover improves.
- Tests/evals to add:
  - Chat integration test: cache hit exists, but final answer still goes through model synthesis.
  - Chat integration test: memory hit exists, but final answer still goes through model synthesis.
  - Failure eval: provider unavailable should yield an honest degradation, not context-snippet prose.
- Done criteria:
  - Memory/cache never directly become the final speaker on chat surfaces.
  - Prior finalized answers are not injected as dominant conversational context by default.
  - Provider outage returns explicit degradation, not stitched retrieval voice.

## 6. Conversation Continuity

- Refs:
  - `storage/dialogue_memory.py:20-42`
  - `storage/dialogue_memory.py:146-167`
  - `storage/dialogue_memory.py:197-235`
  - `core/human_input_adapter.py:213-268`
  - `core/bootstrap_context.py:42-44`
  - `core/bootstrap_context.py:245-261`
  - `core/tiered_context_loader.py:101-118`
  - `core/prompt_normalizer.py:176-205`
  - `apps/nulla_api_server.py:657-677`
  - `core/persistent_memory.py:379-411`
  - `apps/nulla_agent.py:3184-3209`
- Current behavior:
  - `dialogue_turns` stores user-side turn fields only: raw input, normalized input, reconstructed input, topic hints, references, confidence, and quality flags. There is no assistant reply column in `storage/dialogue_memory.py:31-42`.
  - `record_dialogue_turn()` records only user-side fields in `storage/dialogue_memory.py:197-235`.
  - The human-input adapter uses recent user turns and last subject to reconstruct references in `core/human_input_adapter.py:213-268`.
  - Bootstrap context only includes the last two turns in `core/bootstrap_context.py:42-44` and `core/bootstrap_context.py:245-261`.
  - Tiered context dialogue items are built from recent user turns only in `core/tiered_context_loader.py:101-118`.
  - Actual model-visible rolling transcript comes only from `source_context["conversation_history"]` in `core/prompt_normalizer.py:176-205`.
  - The API server supplies `conversation_history` when chat requests come through `/api/chat` in `apps/nulla_api_server.py:657-677`.
  - Assistant outputs are appended to a JSONL log in `core/persistent_memory.py:379-411`, but that log is not the core structured dialogue memory the model reads.
  - Interaction mode is reset aggressively to `smalltalk`, `utility`, or `generic_conversation` in `apps/nulla_agent.py:3184-3209`.
- Why it makes NULLA feel stateless or single-turn:
  - The structured dialogue memory knows what the user said, but not what NULLA said back.
  - Emotional continuity, promises, tone commitments, and prior reasoning are mostly absent from model-visible state unless the upstream surface forwards raw transcript.
  - The core runtime depends on the client to pass history instead of owning conversation state itself.
  - Resetting interaction modes after generic turns makes the runtime feel transactional.
- Required production fix:
  - Add assistant turns to `dialogue_memory` and retrieve them as first-class conversation history.
  - Build a canonical rolling transcript inside the runtime instead of depending on `source_context`.
  - Persist:
    - current topic
    - user intent
    - assistant commitments
    - emotional state / stance
    - unresolved followups
  - Feed a compact rolling transcript to the model on every chat surface.
  - Stop resetting conversational state to a generic bucket after every normal turn.
- Regression risk:
  - Larger context windows.
  - Risk of stale or repeated context if transcript compression is bad.
  - Privacy handling must remain strict when more history is stored.
- Tests/evals to add:
  - Multi-turn eval: pronoun followup must use prior assistant answer, not just prior user noun.
  - Continuity eval: assistant must remember its own last recommendation and refine it next turn.
  - Emotional continuity eval: user frustration on turn N changes tone on turn N+1 without canned fast path.
- Done criteria:
  - Assistant turns are stored in structured dialogue memory.
  - Model history no longer depends solely on client-passed `conversation_history`.
  - Followups work across turns with topic, intent, and emotional continuity.

## 7. Tool-to-Model Relationship

- Refs:
  - `apps/nulla_agent.py:2473-2861`
  - `apps/nulla_agent.py:3402-3461`
  - `apps/nulla_agent.py:1292-1552`
  - `apps/nulla_agent.py:1944-2215`
  - `core/tool_intent_executor.py:152-304`
  - `core/tool_intent_executor.py:307-343`
  - `core/tool_intent_executor.py:346-481`
  - `core/tool_intent_executor.py:572-758`
  - `core/tool_intent_executor.py:771-1155`
  - `core/runtime_execution_tools.py:55-130`
  - `core/runtime_execution_tools.py:142-177`
  - `core/runtime_execution_tools.py:447-495`
  - `core/local_operator_actions.py:101-187`
- Current behavior:
  - Tool-intent path:
    - model chooses tool: yes, through `resolve_tool_intent()` in `apps/nulla_agent.py:2547-2555`
    - model sees raw result: yes, appended to `conversation_history` as `assistant` text in `apps/nulla_agent.py:3402-3421`
    - model synthesizes final answer: yes, second model call in `apps/nulla_agent.py:2810-2819`
    - template bypass remains: yes, via `_tool_loop_final_message()` and `_render_tool_loop_response()` in `apps/nulla_agent.py:3423-3461`
  - Web live-info fast path:
    - model chooses tool: no
    - model sees raw result: no
    - final answer synthesized by model: no
    - template bypass: yes, `apps/nulla_agent.py:1292-1552`
  - Workspace/sandbox via tool-intent:
    - model chooses tool: yes
    - model sees raw result: yes
    - final answer synthesized by model: yes
    - template bypass: yes, runtime tool response templates + tool-loop wrapper
  - Local operator actions via pre-model parse:
    - model chooses tool: no in `apps/nulla_agent.py:419-457`
    - model sees raw result: no
    - final answer synthesized by model: no
    - template bypass: yes, `core/local_operator_actions.py:162-187`
  - Hive list/status/research followups:
    - model chooses tool: no
    - model sees raw result: no
    - final answer synthesized by model: no
    - template bypass: yes, `apps/nulla_agent.py:3211-3258`, `apps/nulla_agent.py:3823-3973`, `apps/nulla_agent.py:4273-4345`
  - Hive tool intents:
    - model chooses tool: yes
    - model sees raw result: yes
    - final answer synthesized by model: yes
    - template bypass: yes, `core/tool_intent_executor.py:771-1155` plus tool-loop wrappers
  - Curiosity/research:
    - model chooses research actions: no
    - raw results become candidates/snippets, not model-chosen tool observations
    - final user answer may or may not be synthesized, depending on later path
- Why it makes NULLA bot-like or less capable:
  - Several high-value tool surfaces bypass model synthesis entirely.
  - Even in the good path, tool results are fed back as assistant-role text and then flattened by wrappers.
  - Tool narration is still template-first in many surfaces.
- Required production fix:
  - For every user-facing tool answer:
    - model must choose or approve the tool path
    - model must see structured tool observations
    - model must synthesize the final reply
  - Replace assistant-role raw tool history with structured observation messages.
  - Remove pre-model operator/Hive/live-info narration from chat surfaces.
  - Keep deterministic tool execution, but not deterministic final voice.
- Regression risk:
  - More model calls on tool answers.
  - Harder to guarantee identical wording in tests.
  - Weak synthesis could misstate tool results if observation formatting is poor.
- Tests/evals to add:
  - One eval per tool surface asserting:
    - tool result exists
    - final answer cites the tool result correctly
    - no template wrapper leaks
  - Failure eval: tool failure produces honest degradation, not fake completion.
- Done criteria:
  - All user-facing tool answers are model-synthesized over structured observations.
  - No major tool surface emits raw template output directly to chat.

## 8. Curiosity Audit

- Refs:
  - `core/curiosity_policy.py:35-52`
  - `core/curiosity_policy.py:55-74`
  - `core/curiosity_roamer.py:79-162`
  - `core/curiosity_roamer.py:262-388`
  - `core/curiosity_roamer.py:410-440`
  - `apps/nulla_agent.py:482-497`
  - `apps/nulla_agent.py:1687-1719`
- Current behavior:
  - Curiosity is policy-bounded with fixed caps for topics, queries, snippets, and total roam seconds in `core/curiosity_policy.py:35-52`.
  - The decision logic is threshold-based, not strategic, in `core/curiosity_policy.py:55-74`.
  - `maybe_roam()` derives topics, queues them, and optionally auto-executes them in `core/curiosity_roamer.py:83-162`.
  - `_execute_topic()` either reuses a cache hit or runs bounded `WebAdapter.search_query()` calls by source profile in `core/curiosity_roamer.py:262-388`.
  - Derived topics are mostly topic-hint condensation, not dynamic research planning, in `core/curiosity_roamer.py:410-440`.
  - The main runtime frontloads curiosity only for certain classes and markers in `apps/nulla_agent.py:1687-1719`.
- Why it makes NULLA less capable:
  - Curiosity is not a strategic research controller. It is a bounded candidate harvester.
  - It cannot dynamically broaden, narrow, or pivot based on intermediate findings.
  - It cannot reason about stopping conditions beyond fixed policy thresholds.
  - It does not create a living research plan the model can revise.
- Required production fix:
  - Replace bounded curiosity as the primary research brain with a model-driven research controller:
    - identify gaps
    - choose next source/tool
    - compare evidence quality
    - decide broaden/narrow/pivot
    - stop on coverage/confidence thresholds
  - Keep the current curiosity subsystem as a low-cost prefetch lane only.
  - Persist reusable research artifacts separately from the final answer voice.
- Regression risk:
  - Research loops can get more expensive and slower.
  - Poor stop criteria can create runaway browsing.
  - Needs strong honesty checks to avoid fake completeness.
- Tests/evals to add:
  - Research eval where the assistant must pivot from one source family to another after poor evidence.
  - Research eval where it must stop after enough corroboration instead of exhausting a fixed budget.
  - Research eval where it must say evidence is insufficient and stop honestly.
- Done criteria:
  - Curiosity is no longer just bounded candidate collection.
  - Research plans can adapt during execution based on evidence quality and coverage.

## 9. Capability Truth Audit

- Refs:
  - `apps/nulla_agent.py:468-675`
  - `apps/nulla_agent.py:1944-2215`
  - `apps/nulla_agent.py:2473-2861`
  - `apps/nulla_agent.py:4700-4724`
  - `core/tool_intent_executor.py:152-304`
  - `core/runtime_execution_tools.py:55-130`
  - `core/runtime_execution_tools.py:447-495`
  - `core/local_operator_actions.py:190-249`
  - `core/curiosity_roamer.py:79-388`
  - `core/parent_orchestrator.py:190-320`
  - `tests/test_nulla_future_vision_spec.py:6-52`

| Capability | Status | Repo proof | Why this status |
| --- | --- | --- | --- |
| can discuss | REAL | `apps/nulla_agent.py:468-675`, `core/prompt_normalizer.py:106-173` | There is a real conversational model path for non-fast-path chat. |
| can research | PARTIAL | `apps/nulla_agent.py:1634-1685`, `core/tool_intent_executor.py:572-758`, `core/curiosity_roamer.py:79-388` | Real web lookup exists, but it is bounded and often template-driven. |
| can act with tools | PARTIAL | `core/tool_intent_executor.py:152-304`, `core/runtime_execution_tools.py:142-177`, `core/local_operator_actions.py:162-187` | Real tools exist, but coverage is narrow and many answers still bypass model synthesis. |
| can verify | PARTIAL | `apps/nulla_agent.py:2161-2183`, `core/runtime_execution_tools.py:447-495` | Verification is real but shallow; builder verification is basically `compileall` for Python. |
| can delegate | FUTURE-ONLY | `tests/test_nulla_future_vision_spec.py:18-27`, `core/parent_orchestrator.py:190-320` | Backend decomposition exists, but chat-level Hive-mind delegation/merge is explicitly xfail. |
| can build | PARTIAL | `apps/nulla_agent.py:1944-2215` | Narrow scaffold builder only. |
| can run code | PARTIAL | `core/runtime_execution_tools.py:118-130`, `core/runtime_execution_tools.py:447-495` | Sandbox command execution is real, but gated and not used as a full autonomous loop. |
| can test | PARTIAL | `apps/nulla_agent.py:2161-2183`, `core/runtime_execution_tools.py:447-495` | Can run bounded commands, but no robust autonomous test-debug-retry cycle. |
| can monitor | PARTIAL | `core/local_operator_actions.py:190-249`, `apps/nulla_agent.py:3520-3603` | Local process/service/disk inspection and Hive presence heartbeat exist, but not a broad monitoring brain. |
| can self-correct | PARTIAL | `core/memory_first_router.py:187-255`, `apps/nulla_agent.py:2661-2698` | There is provider failover and tool-failure fallback, but not a strong autonomous correction loop. |

- Required production fix:
  - Capability claims must be generated from a capability ledger, not hopeful prose.
  - Distinguish:
    - discuss
    - analyze
    - tool-backed act
    - verify
    - delegate
  - Do not let builder/Hive/tool paths imply broader autonomy than exists.
- Regression risk:
  - Marketing-sounding replies will get harsher and more limited until real tooling expands.
- Tests/evals to add:
  - Capability honesty eval corpus with prompts like:
    - `can you deploy this?`
    - `can you ask other agents?`
    - `can you verify this medically?`
  - Fail if the answer implies tooling or verification that the runtime does not actually have.
- Done criteria:
  - Capability claims align 1:1 with real wired behavior.
  - Unsupported abilities are explicitly labeled unsupported or future.

## 10. Hive Reality Audit

- Refs:
  - `apps/nulla_agent.py:3211-3258`
  - `apps/nulla_agent.py:3823-3973`
  - `apps/nulla_agent.py:3987-4120`
  - `apps/nulla_agent.py:4273-4345`
  - `apps/nulla_agent.py:3520-3674`
  - `core/public_hive_bridge.py:67-114`
  - `core/public_hive_bridge.py:116-183`
  - `core/public_hive_bridge.py:265-307`
  - `core/public_hive_bridge.py:308-356`
  - `core/public_hive_bridge.py:358-400`
  - `core/public_hive_bridge.py:402-450`
  - `core/public_hive_bridge.py:451-507`
  - `core/public_hive_bridge.py:622-709`
  - `core/hive_activity_tracker.py:89-145`
  - `core/hive_activity_tracker.py:301-376`
  - `core/parent_orchestrator.py:190-320`
  - `core/task_reassembler.py:118-227`
  - `tests/test_nulla_hive_task_flow.py:8-214`
  - `tests/test_public_hive_bridge.py:510-534`
  - `tests/test_nulla_future_vision_spec.py:18-27`

| Hive capability | Status | Repo proof | Notes |
| --- | --- | --- | --- |
| real task creation | REAL | `core/public_hive_bridge.py:308-356`, `apps/nulla_agent.py:3987-4120` | Real topic write path exists. |
| real task claiming | REAL | `core/public_hive_bridge.py:358-400`, `core/tool_intent_executor.py:954-972`, `tests/test_public_hive_bridge.py:510-534` | Real claim write path exists. |
| real progress posting | REAL | `core/public_hive_bridge.py:402-450`, `core/tool_intent_executor.py:973-995`, `tests/test_public_hive_bridge.py:515-520` | Real post path exists. |
| real artifact/result posting | REAL | `core/public_hive_bridge.py:451-507`, `core/public_hive_bridge.py:265-284`, `tests/test_public_hive_bridge.py:521-534` | Result submission and artifact search are wired. |
| real agent-to-agent communication | PARTIAL | `apps/nulla_agent.py:3520-3603`, `core/public_hive_bridge.py:67-114`, `core/public_hive_bridge.py:622-709` | Presence + commons posts are real, but not direct conversational helper chat. |
| real multi-agent delegation | FUTURE-ONLY | `tests/test_nulla_future_vision_spec.py:18-27`, `core/parent_orchestrator.py:190-320` | Backend swarm plumbing exists, but not a shipped chat/Hive-mind contract. |
| real merge/synthesis of helper outputs | PARTIAL | `core/task_reassembler.py:118-227`, `core/finalizer.py:156-187` | Backend reassembly exists, but user-facing Hive helper merge is not a normal feature. |
| real online/presence truth | PARTIAL | `core/hive_activity_tracker.py:301-376`, `apps/nulla_agent.py:3520-3603` | Real watcher/presence data exists, but freshness/staleness is weak and wording can overstate truth. |

- Why it makes NULLA bot-like or less capable:
  - Hive task writes are mostly real, but the conversational layer still narrates them with fixed templates.
  - Presence language can sound stronger than the underlying watcher/public-lease truth.
  - The runtime implies Hive-mind collaboration more than it actually delivers.
- Required production fix:
  - Add truth labels to Hive claims:
    - watcher-derived
    - public-bridge-derived
    - local-only
    - future/unsupported
  - Keep real write paths, but synthesize final user answers via the model over packet/task evidence.
  - Do not imply multi-agent delegation/merge unless it actually happened in this run.
  - Presence claims must include freshness and source.
- Regression risk:
  - Hive answers will become more cautious and less flashy.
  - Some users may perceive this as weaker until real collaboration ships.
- Tests/evals to add:
  - Presence honesty eval: no unqualified `X agents online` unless watcher/public presence packet is present and fresh.
  - Hive collaboration eval: fail if replies imply helper lanes or merged outputs without actual helper artifacts.
  - Hive write-path tests should remain for create/claim/progress/result.
- Done criteria:
  - Every Hive claim in user chat is source-qualified or tool-backed.
  - No chat reply implies multi-agent delegation or merge unless those artifacts exist in the current run.

## 11. Builder Autonomy Audit

- Refs:
  - `apps/nulla_agent.py:1634-1685`
  - `apps/nulla_agent.py:1944-2215`
  - `apps/nulla_agent.py:2064-2143`
  - `apps/nulla_agent.py:2161-2183`
  - `apps/nulla_agent.py:2459-2471`
  - `tests/test_nulla_future_vision_spec.py:6-15`
- Current behavior:
  - Research input can collect web notes before build in `apps/nulla_agent.py:1634-1685`.
  - Builder path is gated narrowly to system-design/integration requests with workspace write permission in `apps/nulla_agent.py:2032-2062`.
  - Target selection is heuristic and narrow in `apps/nulla_agent.py:2064-2097`.
  - File generation is hardcoded to a few scaffold families in `apps/nulla_agent.py:2099-2143`.
  - Verification is only `python3 -m compileall -q <root>/src` for Python in `apps/nulla_agent.py:2161-2183`.
  - The final answer is a template in `apps/nulla_agent.py:2185-2215`.
  - Unsupported builder asks fall back to a generic researched brief in `apps/nulla_agent.py:2141-2142` and `apps/nulla_agent.py:2459-2471`.
  - The future-spec test explicitly says full `research -> code -> run -> verify` autonomy is not implemented in `tests/test_nulla_future_vision_spec.py:6-15`.
- Why it makes NULLA less capable:
  - The loop stops after scaffold write + shallow verification.
  - There is no real run/inspect/debug/retry/test cycle.
  - Final reporting is template-driven, not an evidence-grounded synthesis over actual execution traces.
- Required production fix:
  - Build a real builder controller:
    - research planner
    - implementation planner
    - file writer
    - runner
    - stdout/stderr inspector
    - patcher
    - retry budget
    - test runner
    - final synthesizer
  - Support artifact collection:
    - file diffs
    - command outputs
    - test failures
    - retry history
  - Keep truthfulness strict: if only scaffolding happened, say only scaffolding happened.
- Regression risk:
  - Bigger blast radius from autonomous file writes and command execution.
  - Longer runtimes.
  - More failure states need clean recovery.
- Tests/evals to add:
  - End-to-end local builder eval:
    - research
    - write files
    - run tests
    - patch failure
    - rerun
    - summarize truthfully
  - Truthfulness eval: if run/test never happened, answer must not imply success.
- Done criteria:
  - NULLA can actually execute `research -> plan -> write -> run -> inspect -> debug -> retry -> test -> summarize`.
  - Final answer cites what ran, what passed, what failed, and what remains.

## 12. Tool Discovery and Tool Composition

- Refs:
  - `core/tool_intent_executor.py:152-304`
  - `core/tool_intent_executor.py:307-343`
  - `core/prompt_normalizer.py:288-307`
  - `apps/nulla_agent.py:2473-2861`
- Current behavior:
  - Tool discovery is a fixed runtime catalog in `core/tool_intent_executor.py:152-304`.
  - Tool-intent is only attempted when heuristics match in `core/tool_intent_executor.py:307-343`.
  - The model is told to choose exactly one tool intent name from the fixed catalog in `core/prompt_normalizer.py:295-306`.
  - Composition exists only as a bounded repeated loop up to 5 steps in `apps/nulla_agent.py:2523-2525`.
  - Unsupported invented tools are rejected in `core/tool_intent_executor.py:466-481`.
- Why it makes NULLA less capable:
  - This is still an intent map with looped selection, not true operator autonomy.
  - The model cannot invent new safe workflows beyond chaining the fixed catalog.
  - There is no tool-gap detection that says `I need X capability; here is the safe tool/skill to add`.
- Required production fix:
  - Add a workflow-planning layer above tool selection:
    - goal decomposition
    - observation schema
    - next-step choice based on prior result
  - Keep the fixed tool catalog for safety, but let the planner compose arbitrary safe sequences over it.
  - Add tool-gap reporting:
    - identify missing capability
    - propose a safe new tool/skill or operator intervention
    - do not pretend the tool exists
- Regression risk:
  - More complicated tool loops can increase error chains.
  - Needs strong idempotency and approval tracking.
- Tests/evals to add:
  - Composition eval: solve a task requiring `workspace.search_text` -> `workspace.read_file` -> `sandbox.run_command` -> final synthesis without a hardcoded branch.
  - Tool-gap eval: ask for an unwired capability; answer must request/propose tooling instead of bluffing.
- Done criteria:
  - Workflow composition is driven by task state, not mostly by heuristic hardcoding.
  - Missing tools are reported honestly with a safe proposal path.

## 13. Safety Split Audit

- Refs:
  - `core/bootstrap_context.py:94-108`
  - `core/prompt_normalizer.py:141-155`
  - `core/prompt_normalizer.py:224-273`
  - `core/execution_gate.py:48-107`
  - `core/execution_gate.py:126-257`
  - `core/runtime_execution_tools.py:447-495`
  - `core/local_operator_actions.py:292-349`
  - `core/local_operator_actions.py:387-489`
  - `core/public_hive_bridge.py:331-332`
  - `core/public_hive_bridge.py:378-379`
  - `core/public_hive_bridge.py:423-424`
  - `core/public_hive_bridge.py:473-474`
  - `core/task_router.py:192-204`
- Current behavior:
  - Conversational prompt carries execution/safety posture in `core/bootstrap_context.py:94-108` and `_tooling_guidance()` in `core/prompt_normalizer.py:224-273`.
  - Execution controls are real and mostly separate in `core/execution_gate.py:48-107` and `core/execution_gate.py:126-257`.
  - Runtime command execution goes through the sandbox + gate in `core/runtime_execution_tools.py:447-495`.
  - Local operator destructive actions require approval through gate logic in `core/local_operator_actions.py:387-489`.
  - Privacy/data exfiltration checks exist in task classification risk flags `core/task_router.py:192-204` and public Hive write guards `core/public_hive_bridge.py:331-332`, `core/public_hive_bridge.py:378-379`, `core/public_hive_bridge.py:423-424`, `core/public_hive_bridge.py:473-474`.
- Why it makes NULLA bot-like or over-restricted in speech:
  - Action safety is already enforced by gates, but the conversation prompt still carries approval and tool fear on normal chat.
  - That makes NULLA sound over-instrumented even when no action is being taken.
  - The runtime is mixing two distinct problems:
    - what NULLA may say
    - what NULLA may do
- Required production fix:
  - Hard split:
    - conversational layer: broad, natural, truth-focused, minimal tone restrictions
    - execution layer: strict tool/side-effect/destructive/privacy gates
  - Only inject approval/destructive/privacy execution rules when the active turn includes or is about action.
  - Keep speech freer; keep actions hard-gated.
- Regression risk:
  - If the conversational prompt becomes too loose without truthfulness constraints, some models may overclaim tools.
  - Needs explicit action-grounding checks before execution.
- Tests/evals to add:
  - Conversational freedom eval: provocative but non-action chat should not get wrapped in execution-policy tone.
  - Action gate eval: destructive commands still require approval regardless of freer conversational prompt.
- Done criteria:
  - Normal chat no longer carries execution-policy tone.
  - Tool and destructive actions remain strictly gated.

## 14. Domain Breadth Audit

- Refs:
  - `core/task_router.py:180-308`
  - `core/task_router.py:405-418`
  - `apps/nulla_agent.py:300-345`
  - `apps/nulla_agent.py:468-675`
- Current behavior:
  - Routing proof from `classify()`:
    - coding/troubleshooting terms -> `debugging` in `core/task_router.py:219-220`
    - broad build/integration prompts -> `system_design` or `integration_orchestration` in `core/task_router.py:228-287`
    - generic research markers -> `research` in `core/task_router.py:268-269`
    - everything else often falls to `unknown` in `core/task_router.py:189-190`, `303-308`
  - `unknown` maps to `summary_block` in `core/task_router.py:413-416`.
  - greeting/how-are-you/date already collapse into fast paths in `apps/nulla_agent.py:300-345`.
  - I sampled the classifier locally on 2026-03-13:
    - coding -> `debugging`
    - business strategy -> `unknown`
    - research -> `research`
    - food/nutrition -> `unknown`
    - relationships/intimate -> `unknown`
    - general chat -> `unknown` or pre-model smalltalk depending wording
    - creative ideation -> `unknown`
    - troubleshooting -> `debugging`
- Why it makes NULLA topic-dependent and bot-like:
  - Coding/troubleshooting at least get a dedicated lane, even if still planner-shaped.
  - Business strategy, food, relationships, creative ideation, and broad conversation mostly fall into `unknown`, which still becomes `summary_block`.
  - General chat either hits canned fast paths or a structured summarizer.
- Required production fix:
  - Add a dedicated conversational synthesis class for ordinary chat and broad advisory domains.
  - Keep domain-specific planners only when the user explicitly wants a plan/workflow.
  - Route business strategy, food/nutrition, relationships/intimate, creative ideation, and general chat through the same AI-first plain-text answer path.
- Regression risk:
  - Classifier changes can reduce specificity on technical tasks if done carelessly.
- Tests/evals to add:
  - Domain corpus eval with at least:
    - coding
    - business strategy
    - research
    - food/nutrition
    - relationships/intimate
    - general chat
    - creative ideation
    - troubleshooting
  - Assert all non-command chat domains hit plain-text model synthesis and do not leak planner wrappers unless asked.
- Done criteria:
  - The same AI-first chat path handles broad non-command domains.
  - Topic changes no longer cause the runtime to collapse into bot mode.

## 15. Evaluation Suite Redesign

- Refs:
  - `tests/test_nulla_runtime_contracts.py:10-31`
  - `tests/test_nulla_runtime_contracts.py:34-68`
  - `tests/test_nulla_web_freshness_and_lookup.py:18-55`
  - `tests/test_nulla_web_freshness_and_lookup.py:74-100`
  - `tests/test_openclaw_tooling_context.py:161-199`
  - `tests/test_nulla_hive_task_flow.py:8-32`
  - `tests/test_nulla_future_vision_spec.py:6-52`
- Current behavior:
  - The current tests do prove and protect canned/fast-path behavior:
    - date/day fast path must not load context in `tests/test_nulla_runtime_contracts.py:10-31`
    - evaluative canned phrases are asserted in `tests/test_nulla_runtime_contracts.py:34-55`
    - repeated canned greeting variants are asserted in `tests/test_nulla_runtime_contracts.py:58-68`
    - fresh web lookup fast path is asserted in `tests/test_nulla_web_freshness_and_lookup.py:18-55`
    - weather fast path must not load context in `tests/test_nulla_web_freshness_and_lookup.py:74-100`
    - news/weather live-info utility templates are asserted in `tests/test_openclaw_tooling_context.py:161-199`
    - Hive task list template is asserted in `tests/test_nulla_hive_task_flow.py:8-32`
  - The future tests explicitly mark real autonomy gaps as xfail in `tests/test_nulla_future_vision_spec.py:6-52`.
- Why it makes NULLA bot-like or less capable:
  - The test suite rewards fast-path determinism and template stability.
  - That makes removing bot behavior look like a regression.
- Required production fix:
  - Replace behavior-locking tests with AI-first contract tests.
  - Keep only deterministic utility tests for lanes explicitly chosen to remain deterministic.
  - New test suite should protect:
    - ordinary chat hits model
    - live-info uses evidence + model synthesis
    - no fake capabilities
    - no fake Hive participation
    - no planner leakage
    - tool failures degrade honestly
    - no greeting lock-in
- Tests/evals to add:
  - `tests/test_ai_first_chat_runtime.py`
    - `test_greeting_hits_model_not_fast_path`
    - `test_how_are_you_hits_model_not_canned_status`
    - `test_business_strategy_uses_plain_text_synthesis`
    - `test_food_question_uses_plain_text_synthesis`
    - `test_relationship_question_uses_plain_text_synthesis`
  - `tests/test_ai_first_live_info.py`
    - `test_live_info_answer_is_model_synthesized_over_notes`
    - `test_live_info_failure_is_honest_and_unwrapped`
  - `tests/test_capability_honesty.py`
    - `test_unwired_tool_claims_are_refused`
    - `test_multi_agent_claims_require_real_artifacts`
  - `tests/test_hive_truth_levels.py`
    - `test_presence_claim_includes_source_and_freshness`
    - `test_hive_status_uses_packet_evidence_and_model_synthesis`
  - `tests/test_no_planner_leakage.py`
    - `test_no_summary_block_wrapper_in_chat`
    - `test_no_real_steps_completed_wrapper_in_chat`
    - `test_no_workflow_prefix_in_chat`
  - `tests/test_tool_failure_honesty.py`
    - `test_invalid_tool_intent_degrades_honestly`
    - `test_tool_failure_does_not_claim_success`
- Done criteria:
  - The test suite protects AI-first behavior instead of canned output stability.
  - Removing chat fast paths no longer breaks the core acceptance suite.

## 16. Acceptance Criteria for “NULLA Is Actually AI-First”

- Refs:
  - `apps/nulla_agent.py:300-345`
  - `apps/nulla_agent.py:468-675`
  - `apps/nulla_agent.py:1292-1552`
  - `apps/nulla_agent.py:3211-3258`
  - `apps/nulla_agent.py:3423-3461`
  - `core/task_router.py:405-418`
  - `core/memory_first_router.py:89-115`
- Current behavior:
  - Normal conversation still loses too many turns to non-model or planner-shaped paths.
- Required production fix:
  - Ship only when all of these hold:
    - `>= 95%` of non-command chat-surface turns call the model for the final answer.
    - `0` canned greeting/evaluative responses on standard chat path.
    - `0` chat-surface live-info responses emitted directly by `_render_live_info_response()`.
    - `100%` of live-info answers are either model-synthesized over evidence or explicit honest failure.
    - `0` chat answers with `summary_block` / `action_plan` / `Real steps completed:` / `Workflow:` leakage unless the user explicitly asked for that form.
    - `0` final chat answers sourced directly from cache or memory without final synthesis.
    - `100%` of Hive participation/presence claims include source level or freshness if they rely on watcher/public presence.
    - `100%` of capability claims beyond normal discussion require tool-backed proof from the current run or a stable capability ledger.
    - `>= 90%` pass rate on the domain-breadth eval corpus across coding, business, research, food, relationships, general chat, creative ideation, troubleshooting.
- Regression risk:
  - More model usage, slower response, more provider sensitivity.
- Tests/evals to add:
  - CI metrics harness that records:
    - model call rate
    - fast-path rate
    - planner leakage rate
    - tool-backed claim rate
    - Hive truth-label coverage
- Done criteria:
  - The thresholds above are measured and green in CI and local proof runs.

## 17. Prioritized Patch Sequence

- P0 must-fix:
  - Remove chat-surface fast paths for greeting/evaluative/help/live-info/Hive wording.
  - Change normal chat from `summary_block` / `action_plan` to `plain_text`.
  - Stop using `build_plan()` / `render_response()` as the user-facing chat renderer.
  - Stop emitting `_render_live_info_response()`, Hive templates, and `_workspace_build_response()` directly to users.
  - User-visible improvement: NULLA immediately stops sounding like a scripted wrapper.
  - Regression risk: biggest behavioral churn; existing tests will fail.
- P1 core quality:
  - Store assistant turns in structured dialogue memory and feed real rolling history.
  - Remove memory/cache as direct speakers.
  - Split conversation prompt from execution policy prompt.
  - Remove tool-loop and workflow wrappers from normal chat.
  - User-visible improvement: continuity and tone stabilize across turns.
  - Regression risk: context bloat and prompt-tuning mistakes.
- P2 breadth/autonomy:
  - Add AI-first routing for business/food/relationships/creative/general chat.
  - Build real research controller and true builder loop.
  - Add tool-gap reporting and better tool composition.
  - User-visible improvement: broader domains feel like one intelligence, not a coding bot with side templates.
  - Regression risk: complexity and runtime cost rise sharply.
- P3 future scale:
  - Ship real chat-level multi-agent delegation + merge.
  - Add real user approval/outcome personalization loop.
  - Add eval dashboards and continuous regression gating on AI-first metrics.
  - User-visible improvement: true operator/autonomy layer and adaptive long-horizon behavior.
  - Regression risk: distributed-state complexity and truthfulness failures if rushed.

## 18. Plan Gaps: What Was Missing Before

- Current behavior:
  - The earlier plan was directionally right, but it did not fully cover the concrete repo failures now visible in code.
- What was missing and why it matters:
  - Prompt stack specifics:
    - I had not yet mapped the exact injection chain through `bootstrap_context`, `tiered_context_loader`, `prompt_normalizer`, and `internal_message_schema`.
    - That matters because the over-constraint is not one line. It is the full stack.
  - Model settings and forced output contracts:
    - I had not explicitly called out that `unknown` -> `summary_block` and most technical classes -> `action_plan`, with `temperature=0.1`.
    - That is a primary reason NULLA sounds planner-shaped even when the model is running.
  - Post-model flatteners:
    - I had not fully enumerated `build_plan()`, `render_response()`, `render_with_persona()` truncation, tool wrappers, Hive regex rewrites, and workflow wrappers.
    - That matters because removing fast paths alone would not fix the bot-shell.
  - Conversation continuity failure mode:
    - I had not yet pinned that structured dialogue memory stores user turns but not assistant turns.
    - That is one of the cleanest explanations for the stateless feel.
  - Tool composition limits:
    - I had not yet separated `fixed intent map with bounded loop` from real operator autonomy.
    - That matters because saying `tool use works` would still overstate what the runtime can invent on its own.
  - Domain breadth collapse:
    - I had not yet shown that business/food/relationships/creative/general chat mostly fall into `unknown` and therefore `summary_block`.
    - That matters because the AI-first fix is not only for greetings. It is for broad ordinary conversation.
  - Eval redesign details:
    - I had not yet translated the diagnosis into exact replacement tests and measurable thresholds.
    - That matters because otherwise the repo will regress back into canned behavior.
- Required production fix:
  - Treat this audit doc as the real implementation contract, not the earlier high-level plan.
- Regression risk:
  - If patching starts from the older high-level plan alone, the repo will keep the planner shell, continuity loss, and structured-output choke points.
- Tests/evals to add:
  - Use sections 15 and 16 of this doc as the acceptance suite design and ship gate source of truth.
- Done criteria:
  - The implementation plan is now derived from exact runtime choke points, not just product intuition.
