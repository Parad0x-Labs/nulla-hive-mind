# NULLA Alpha Diagnostic Audit — Pass 1

**Date:** 2026-03-12  
**Scope:** Diagnosis only. No feature work, no roadmap, no fixes. Root-cause map for remaining "retard bot" failures.  
**Method:** Trace exact codepaths, prove where failures originate.

---

## Section 1: Confirmed Failures

### Failure 1: "Hive Mind is not enabled in this runtime" on fresh session + Hive query

**Transcript:**
1. Fresh session: "A new session was started via /new or /reset..." → assistant: "I'm NULLA. New session is clean and I'm ready. What do you want to do?"
2. User: "hi check hive mind pls" → assistant: "I'm NULLA. The Hive Mind is not enabled in this runtime."
3. User: /new again.
4. User: "hi what is on the hive mind tasks ?" → assistant: "The Hive Mind is not enabled in this runtime."

**Note:** The exact string "The Hive Mind is not enabled in this runtime" does not appear in the codebase. The codebase has:
- `"Hive watcher is not configured on this runtime, so I can't report real live Hive state. Hive truth: future/unsupported."` (hive_activity_tracker.py:106)
- `"Public Hive is not enabled on this runtime, so I can't claim a live Hive task."` (nulla_agent.py:6102, 6359, 6621)

So the user-facing text is either a **model paraphrase** of one of these, or comes from a different code path / surface. The audit treats the underlying behavior: Hive queries return a "not enabled/not configured" style response when they should return a task list or bridge fallback.

---

### Failure 2: Selecting shown Hive task by full title or short #id re-lists tasks instead of starting

**Transcript:** User selected a shown Hive task by full title and short #id, but assistant re-listed tasks instead of starting the chosen one.

---

### Failure 3: "what time is now in Vilnius?" produced placeholder output like [time]

**Transcript:** Time utility returned placeholder instead of bound value.

---

### Failure 4: Planner junk leaked (review problem, choose safe next step, validate result)

**Transcript:** Internal planner scaffold leaked into user chat.

---

### Failure 5: Malformed Vilnius followup collapsed into generic brochure text

**Transcript:** "what where's is in Vilnius?" (malformed) produced generic brochure instead of in-context recovery.

---

## Section 2: Root Causes

### A. SESSION STARTUP / RESET

#### A.1 Codepath for /new and /reset

**File:** `apps/nulla_agent.py`

| Line | Code | Behavior |
|------|------|----------|
| 2437–2446 | `_ui_command_fast_path` | `/new`, `/new-session`, `/clear`, `/reset` → returns: "Use the OpenClaw `New session` button on the lower right. Slash `/new` is not a wired command in this runtime." |
| 2447–2456 | `_startup_sequence_fast_path` | Fires when `user_input` contains BOTH "new session was started" AND "session startup sequence". Returns: "I'm {name}. New session is clean and I'm ready. What do you want to do?" |

**Finding:** `/new` and `/reset` are **not** wired as agent commands. The agent tells the user to use the OpenClaw "New session" button. The startup sequence fires only when the **client** sends the literal prompt: "A new session was started via /new or /reset. Execute your Session Startup sequence now - read the required files before responding to the user." That prompt is injected by the OpenClaw control UI, not by the user.

**State cleared/preserved:** The agent does not clear session state on `/new`. Session state (hive interaction, pending topics, etc.) lives in `session_hive_watch_state` and is keyed by `session_id`. When the client starts a "new session," it typically uses a new `session_id`. So state is effectively cleared by session ID change, not by an explicit reset handler in the agent.

**Startup by surface:** `_startup_sequence_fast_path` does not branch by surface. It checks only the normalized user input. No surface-specific startup logic.

**Startup file reading:** The startup prompt says "read the required files" but `_startup_sequence_fast_path` returns a **deterministic string** without loading SOUL.md, USER.md, or any files. The agent never executes the "Session Startup sequence" as described in AGENTS.md. **Root cause:** Startup is a canned reply, not a real startup sequence.

---

### B. SURFACE / RUNTIME DETECTION

#### B.1 How NULLA decides runtime/surface

**File:** `apps/nulla_api_server.py:354-356`

```python
base_context = {
    "surface": "channel",
    "platform": "openclaw",
    ...
}
```

**Finding:** The API server **hardcodes** `surface="channel"` and `platform="openclaw"` for all requests. There is no detection of `openclaw-control-ui` vs other surfaces. The client can override via `source_context`, but the default is fixed.

#### B.2 openclaw-control-ui vs other surfaces

**Finding:** No code path distinguishes `openclaw-control-ui`. The only reference is in `installer/register_openclaw_agent.py:408` for `controlUi.allowedOrigins`. No agent logic branches on this.

#### B.3 Hive enabled/disabled gating

**File:** `core/hive_activity_tracker.py:515-536`

```python
def load_hive_activity_tracker_config() -> HiveActivityTrackerConfig:
    watcher_url = os.environ.get("NULLA_HIVE_WATCHER_URL") or _load_watcher_url_from_manifest()
    api_url = _watcher_api_url(watcher_url)
    enabled_raw = os.environ.get("NULLA_HIVE_FOLLOWUPS_ENABLED", "1")
    ...
    return HiveActivityTrackerConfig(
        enabled=enabled_raw not in {"0", "false", "no", "off"} and bool(api_url),
        watcher_api_url=api_url,
        ...
    )
```

**Finding:** Hive tracker is enabled only if `api_url` is non-empty. If `NULLA_HIVE_WATCHER_URL` is unset and `_load_watcher_url_from_manifest()` returns empty (no cluster_manifest.json with watcher URL), then `api_url` is None and `enabled` is False.

**File:** `core/public_hive_bridge.py:58-59`

```python
def enabled(self) -> bool:
    return bool(self.config.enabled and self.config.meet_seed_urls)
```

**Finding:** Public Hive bridge is enabled only if `meet_seed_urls` is non-empty. That comes from `load_public_hive_bridge_config()` which reads agent bootstrap / env. If no meet seed URLs are configured, bridge is disabled.

#### B.4 "Hive Mind is not enabled" — true config vs misclassification

**Proven:** When both are true:
1. `watcher_api_url` is None (no env, no manifest)
2. `meet_seed_urls` is empty (no public bridge config)

Then:
- Tracker returns: "Hive watcher is not configured on this runtime, so I can't report real live Hive state. Hive truth: future/unsupported."
- `_hive_tracker_needs_bridge_fallback` is True (response starts with "hive watcher is not configured")
- `_maybe_handle_hive_bridge_fallback` is called
- It returns None because `public_hive_bridge.enabled()` is False (no meet_seed_urls)
- User gets the raw tracker message

**Conclusion:** On a runtime with no Hive watcher URL and no public bridge seed URLs, "not enabled/not configured" is the **correct** config state. The failure is **config/env**, not a code bug. If the user expects Hive to work, the runtime must have `NULLA_HIVE_WATCHER_URL` or a cluster manifest with a watcher URL, and/or meet_seed_urls in agent bootstrap.

**Caveat:** If the user sees "The Hive Mind is not enabled" (exact wording), that may be a **model paraphrase** when the response goes through `_chat_surface_hive_wording_result` or model synthesis. The model can rephrase the system message.

---

### C. HIVE AVAILABILITY / TASK FLOW

#### C.1 Codepath for "check hive mind pls"

**File:** `core/hive_activity_tracker.py`

| Pattern | Matches "check hive mind pls"? |
|---------|-------------------------------|
| `(?:show\|list\|check)\s+...(?:hive\|hive mind)...\s+(?:tasks\|research\|researches)` | No — requires "tasks" or "research" after "hive mind" |
| `(?:check\|show)\s+(?:the\s+)?hive mind tasks` | No — requires "tasks" at end |
| `_looks_like_hive_overview_request` | No — requires "online" or "agents" in text |

**File:** `apps/nulla_agent.py:5330-5372` — `_recover_hive_runtime_command_input`

Recovery requires:
- `has_task_marker` = any of ("task", "tasks", "taks", "work") in lowered
- `has_inquiry_marker` = any of ("check", "see", "show", "what", ...) in lowered

"check hive mind pls" has "check" but **not** "task", "tasks", "work". So `has_task_marker` is False. Recovery returns `""`.

**Root cause:** "check hive mind pls" **does not match** any Hive pattern and **does not qualify** for recovery. It falls through to the full task path (classification, tool intent, model). The model then produces a response. If the model has seen "Hive not enabled" style text in training or system context, it may output that.

**Severity:** Alpha blocker — natural Hive phrasing is not recognized.

#### C.2 Codepath for "what is on the hive mind tasks?"

**Recovery:** Has "tasks" and "what" → `_recover_hive_runtime_command_input` returns `"show me the open hive tasks"`.

**Second call:** `_maybe_handle_hive_runtime_command("show me the open hive tasks", ...)`.

**Pattern match:** `_looks_like_hive_pull_request` — pattern `(?:show|list|check)\s+(?:the\s+)?(?:available\s+)?(?:hive|hive mind|...)\s+(?:tasks|...)` — "show me the open hive tasks" has "me" between "show" and "the", so the strict pattern may not match. Pattern `(?:show|list|check|what(?:'s| is))\s+(?:the\s+)?(?:available\s+)?(?:tasks?|queue|work)\b.*\b(?:hive|...)` — requires "tasks" before "hive" in the string. "show me the open hive tasks" has "hive" before "tasks". So it may not match.

**Verification needed:** Run a test with "show me the open hive tasks" and confirm whether the tracker handles it. The test `test_show_open_hive_tasks_returns_real_list_not_fake_planner_sludge` may use mocks.

**If tracker matches:** User gets task list or "not configured" if watcher/bridge disabled.  
**If tracker misses:** Falls through to full path; model responds.

#### C.3 Selecting shown task by full title or short #id re-lists instead of starting

**File:** `apps/nulla_agent.py:6168-6190` — `_select_hive_research_signal`, `_interaction_shown_titles`, `_interaction_pending_topic_ids`

**Logic:** `_select_hive_research_signal` matches user input against:
- `pending_topic_ids` from session state
- `shown_titles` from `_interaction_shown_titles(hive_state)`
- Topic hint from `_extract_hive_topic_hint(clean)`

**File:** `apps/nulla_agent.py` — `_interaction_shown_titles` reads `interaction_payload.shown_titles` from session hive state.

**Root cause candidates:**
1. **Shown-task payload not persisted:** After rendering a task list, `interaction_payload` with `shown_titles` and `shown_topic_ids` may not be written to session state.
2. **Followup resolver mismatch:** `_select_hive_research_signal` may not match "Agent Commons: better watcher UX" or "#7d33994f" to the correct topic_id.
3. **Reset wiping state:** /new or session reset may clear `interaction_payload` before the user can select.
4. **Wrong interaction_mode:** If `interaction_mode` is not `hive_task_selection_pending` or similar, the followup handler may not run.

**File refs:** `apps/nulla_agent.py:6091-6099` (hive research followup guard), `core/hive_activity_tracker.py:356-369` (interaction_payload update).

---

### D. TIME / UTILITY BINDING

#### D.1 Codepath for "what time is now in Vilnius?"

**File:** `apps/nulla_agent.py:2367-2417` — `_date_time_fast_path`

- `asks_time` is True when "time" in cleaned and ("what", "now", "current", "right now") or requested_timezone.
- `_extract_utility_timezone` uses `_UTILITY_TIMEZONE_ALIASES` — "vilnius" → ("Europe/Vilnius", "Vilnius").
- `_utility_now_for_timezone("Europe/Vilnius")` → `datetime.now(ZoneInfo("Europe/Vilnius"))`.
- Response: `now.strftime(f"Current time in {requested_label} is %H:%M %Z.")`.

**Finding:** The date/time fast path **never** emits `[time]`. It uses `strftime` with real values. The `[time]` placeholder must come from:
1. A different code path (e.g. model output, reasoning engine, template)
2. A prompt template with `[time]` that is not substituted
3. A degraded fallback when the fast path is skipped

**Search:** `[time]` does not appear as a literal in the codebase. Tests assert it must not appear. So the source is likely **model output** or an **external template** (e.g. in OpenClaw or a prompt).

**Root cause:** Unproven in codebase. Likely: model or prompt template emits `[time]` when the time utility path is not taken (e.g. malformed input, wrong classification, or model used instead of fast path).

---

### E. PLANNER LEAKAGE

#### E.1 Sources of "review problem", "choose safe next step", "validate result"

**File:** `core/reasoning_engine.py:139`

```python
return mapping.get(task_class, ["review_problem", "choose_safe_next_step", "validate_result"])
```

For unknown `task_class`, fallback steps are `review_problem`, `choose_safe_next_step`, `validate_result`. These are converted to readable form: `s.replace("_", " ")` → "review problem", "choose safe next step", "validate result".

**File:** `core/reasoning_engine.py:388-402` — `_build_natural_fallback`

```python
generic_steps = {"review problem", "choose safe next step", "validate result"}
if set(readable_steps) == generic_steps:
    return "I'm here and ready to help. What would you like to work on?"
step_text = "\n".join(f"- {s}" for s in readable_steps)
if not allow_planner_style:
    return step_text
return f"Here's what I'd suggest:\n\n{step_text}"
```

**Finding:** When `allow_planner_style` is True and steps are the generic set, the reasoning engine returns the step text. That text can reach the user if not sanitized.

**File:** `apps/nulla_agent.py:5131-5145` — `_contains_generic_planner_scaffold`

The agent has logic to detect and strip generic planner scaffold. It is used in `_shape_user_facing_text` and related sanitization.

**Root cause:** Planner leakage occurs when:
1. The reasoning engine output is returned to the user without passing through `_shape_user_facing_text` / `_contains_generic_planner_scaffold`.
2. A code path returns raw plan output.
3. `allow_planner_style` is True and the sanitization path is skipped for that response class.

**File refs:** `apps/nulla_agent.py:2956` (`_strip_runtime_preamble`), `apps/nulla_agent.py:5131` (`_contains_generic_planner_scaffold`), `core/reasoning_engine.py:395`.

---

### F. MALFORMED FOLLOWUP / INTENT RECOVERY

#### F.1 "what where's is in Vilnius?" → generic brochure

**Transcript:** Malformed time followup produced generic brochure instead of in-context recovery.

**Codepath:** "what where's is in Vilnius?" — typo/grammar. `_date_time_fast_path`:
- `asks_time` requires "time" in cleaned or requested_timezone. "what where's is in Vilnius" has "Vilnius" but may not have "time".
- `_extract_utility_timezone` finds "vilnius" → ("Europe/Vilnius", "Vilnius").
- `asks_time = (requested_timezone and "time" in cleaned)` — "time" is not in "what where's is in vilnius". So `asks_time` may be False.
- `asks_date` — no date markers. So both False → fast path returns None.

**Finding:** Malformed input misses the date/time fast path. Falls through to full path. Model receives the malformed prompt. If conversation_history has a prior time-related exchange, the model could recover. If not, or if context is weak, the model may produce generic text.

**Root cause:** No explicit malformed-followup repair. The system relies on the model and context. If context is missing or the model fails, generic output results.

---

## Section 3: Surface/Runtime Truth Table

| Condition | Hive Tracker | Hive Bridge Fallback | User sees |
|-----------|--------------|----------------------|-----------|
| watcher_url set, bridge meet_seed_urls set | OK or unreachable | OK if tracker fails | Task list or bridge list |
| watcher_url set, bridge empty | OK or unreachable | No fallback | "not configured" if tracker fails |
| watcher_url empty, bridge set | "not configured" | Fallback runs | Bridge task list |
| watcher_url empty, bridge empty | "not configured" | No fallback | "Hive watcher is not configured on this runtime..." |

**Config sources:**
- `NULLA_HIVE_WATCHER_URL` (env)
- `cluster_manifest.json` (via `_load_watcher_url_from_manifest`)
- Agent bootstrap `meet_seed_urls` (for bridge)

---

## Section 4: Test Gaps

| Failure | Covered? | Gap |
|---------|----------|-----|
| Fresh session + Hive query on openclaw-control-ui | No | No test for this exact flow |
| /new then Hive task list | Partial | Startup test exists; no test for /new → Hive in same session |
| Selecting shown Hive task by full name | No | No test for selection by full title |
| Selecting shown Hive task by short #id | Partial | `test_openclaw_can_select_specific_hive_task_by_short_id` exists; may use mocks |
| Vilnius time returns real value, never [time] | Yes | `test_utility_time_in_vilnius_binds_real_value_and_never_leaks_placeholder` |
| Planner junk never leaks | Partial | `test_sanitization_contract_strips_runtime_preamble_and_forbidden_tool_garbage` uses specific text; no full-path test |
| Malformed followup recovers in-context | Partial | `test_malformed_vilnius_time_followup_recovers_to_bound_utility_answer` — may not cover "what where's is" |
| "check hive mind pls" → Hive path | No | `test_nulla_hive_task_flow` uses "check hive mind see if any taks is up" which is recovered; "check hive mind pls" is not |
| "what is on the hive mind tasks" → Hive path | No | Recovery path tested indirectly; exact phrase not tested |

**Exact tests to add:**
1. `test_fresh_session_then_hive_query_on_openclaw_returns_task_list_or_honest_degradation` — fresh session_id, then "check hive mind pls" or "what is on the hive mind tasks", assert Hive path or honest "not configured", never model hallucination.
2. `test_select_shown_hive_task_by_full_title_starts_research` — list tasks, then send full title as followup, assert start research.
3. `test_select_shown_hive_task_by_short_id_starts_research` — list tasks, then send #id, assert start research.
4. `test_check_hive_mind_pls_matches_hive_path_or_recovers` — "check hive mind pls" either hits Hive path or is recovered to "show me the open hive tasks".
5. `test_planner_scaffold_never_leaks_in_any_response_class` — for multiple response classes, assert "review problem", "choose safe next step", "validate result" never appear.
6. `test_malformed_time_followup_what_wheres_is_in_vilnius_recovers` — conversation_history with prior time exchange, "what where's is in Vilnius?", assert time-bound answer or clear recovery.

---

## Section 5: Minimal Bugfix Order

**Order (diagnosis-first; no speculative fixes):**

1. **Config verification:** Confirm on the failing runtime: `NULLA_HIVE_WATCHER_URL`, cluster manifest, agent bootstrap `meet_seed_urls`. If all empty, "not enabled" is expected. Document required config for alpha.
2. **Hive pattern gap:** Add "check hive mind" (without "tasks") to `_HIVE_PULL_PATTERNS` or `_recover_hive_runtime_command_input` so "check hive mind pls" recovers to "show me the open hive tasks".
3. **Shown-task selection:** Trace `_store_hive_topic_selection_state` and `_interaction_shown_titles`; ensure `shown_titles` and `shown_topic_ids` are persisted when rendering task list; ensure `_select_hive_research_signal` matches full title and short #id.
4. **[time] placeholder:** Add logging when date/time fast path is skipped; trace model prompt for any `[time]` template; fix substitution or path.
5. **Planner leakage:** Audit all return paths from reasoning engine to user; ensure `_shape_user_facing_text` / `_contains_generic_planner_scaffold` is applied to every user-facing response.
6. **Malformed followup:** Add explicit repair for time-related malformed input (e.g. "what where's is in Vilnius" → treat as "what time is it in Vilnius") or strengthen context injection for followups.

---

## Summary Table

| Failure | Root cause type | Severity | File(s) |
|---------|-----------------|----------|---------|
| Hive "not enabled" on fresh session | Config (watcher + bridge empty) or pattern gap ("check hive mind pls") | Alpha blocker | hive_activity_tracker.py, nulla_agent.py, public_hive_bridge.py |
| Select task re-lists | State machine / persistence | Serious | nulla_agent.py, hive_activity_tracker.py |
| [time] placeholder | Unproven (model/template) | Serious | — |
| Planner leakage | Sanitization path gap | Serious | reasoning_engine.py, nulla_agent.py |
| Malformed Vilnius → brochure | No repair, weak context | Annoying | nulla_agent.py |
| Startup is canned, not real sequence | Design choice | Annoying | nulla_agent.py |

---

**End of Pass 1. No code changes in this audit.**
