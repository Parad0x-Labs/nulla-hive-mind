# AI-First Alpha Hardening Pass 2

## Goal

Run a live-soak hardening pass over the current alpha lane with the real local provider where possible, and with live-like upstream noise where direct live inputs are unsafe or not controllable.

This pass is not feature expansion.
It is a release gate.

## Scope

Pass 2 covers:

- live-provider variability on ordinary chat
- longer multi-turn continuity drift
- live-info under noisier evidence
- Hive truth under stale, missing, or degraded inputs
- bounded builder repeated-run soak
- provider failure / empty output / honest degradation
- alpha release checklist draft

It does not include any `M4` work.

## Live-Soak Design

### Real provider

The gauntlet uses the real local Ollama model `qwen2.5:7b` through NULLA's real `MemoryFirstRouter` and `NullaAgent.run_once()` path.

### Live-like upstream control

Some inputs are intentionally controlled while still using the real provider:

- noisy live-info evidence is injected through mocked web results
- stale and missing Hive states are injected through mocked watcher / public-bridge payloads
- builder loops use the real bounded controller with deterministic mocked tool execution
- degradation cases force provider/cache/memory failure states to verify honesty

That split is deliberate:

- use the real model where variability matters
- control the upstream state where safety or reproducibility matters

## Ship-Blocking Thresholds

| Check | Threshold | Corpus |
| --- | --- | --- |
| live model-final hit rate | `>= 87.5%` | 8 broader non-command prompts |
| live continuity drift | `>= 75%` | 2 longer followup sequences |
| live-info under noisy evidence | `100%` | 2 noisy evidence prompts |
| Hive truth edge cases | `100%` | 4 degraded/fallback Hive cases |
| builder repeated truth | `100%` | 3 repeated bounded builder runs |
| honest degradation | `100%` | 3 provider/cache/memory failure cases |

## Corpus Summary

### Ordinary chat corpus

8 prompts:

- greeting
- evaluative
- general reflection
- business strategy
- food / nutrition
- relationships
- creative ideation
- coding / troubleshooting

### Continuity corpus

2 sequences:

- preserve a prior comparison topic
- clear stale topic state after a topic shift

### Live-info corpus

2 prompts:

- latest Telegram Bot API updates
- current weather in London

Both use noisy evidence mixes instead of clean single-source notes.

### Hive edge-case corpus

4 cases:

- watcher-derived + stale presence
- public-bridge-derived status
- local-only fallback
- future / unsupported followup

### Builder soak corpus

3 runs:

- scaffold flow run A
- scaffold flow run B
- retry / repair flow

### Honest degradation corpus

3 cases:

- no provider available
- provider returns unusable output
- live-info memory path blocked from speaking as fresh output

## Commands

### Pass 2 live-soak only

```bash
NULLA_ALPHA_LIVE_SOAK=1 pytest -q tests/test_alpha_hardening_pass2_live_soak.py
```

### Combined alpha-lane gauntlet

```bash
NULLA_ALPHA_LIVE_SOAK=1 pytest -q \
  tests/test_alpha_hardening_pass1_gauntlet.py \
  tests/test_alpha_hardening_pass2_live_soak.py \
  tests/test_milestone1_ai_first_evals.py \
  tests/test_dialogue_continuity_state.py \
  tests/test_runtime_continuity.py \
  tests/test_runtime_capability_ledger.py \
  tests/test_nulla_hive_task_flow.py \
  tests/test_nulla_web_freshness_and_lookup.py \
  tests/test_nulla_chat_truth_instrumentation.py
```

## Measured Results

### Pass 2 live-soak

```text
6 passed, 1 warning
runtime: 434.70s
```

### Combined alpha-lane gauntlet

```text
90 passed, 1 warning
runtime: 589.92s
```

## Failures Found

### 1. Live-info template shell still reachable on the real alpha lane

Observed from the live daemon before the runtime fix:

- `latest telegram bot api updates` returned `Live web results for ...`

Root cause:

- chat-surface model wording for direct helper paths was not reusing the main chat-surface routing profile
- live-info helper calls could still hit structured/planner-shaped provider settings

Fix:

- route `_chat_surface_model_wording_result()` through `_model_routing_profile()`

### 2. Explicit plan requests could still be polluted by adaptive research

Observed during combined gauntlet:

- explicit plan request could drift into research-shaped plan text before the planner renderer contract was checked

Root cause:

- adaptive research still ran for explicit planner-style requests

Fix:

- skip adaptive research on chat surfaces when `planner_style_requested=true`

### 3. Two gauntlet assertions were too brittle

These were harness issues, not product bugs:

- live-info degradation expected one exact sentence instead of any honest degraded reply
- explicit plan regression expected one exact planner summary instead of the real planner-renderer contract

Fix:

- relaxed assertions to test the actual contract, not one sentence

## Alpha Release Checklist Draft

Release only if all are true:

- combined alpha-lane gauntlet is green
- live-soak gauntlet is green
- local provider `qwen2.5:7b` is present and healthy
- non-command chat still clears the live model-final threshold
- no planner-wrapper regressions appear in the live ordinary-chat corpus
- Hive degraded/fallback cases stay source-qualified
- builder repeated runs still cite artifacts, failures, retries, and stop reason
- degraded paths stay honest and do not recycle cache/memory as fresh speech

Operational checks before invite-only alpha:

- verify the running daemon has been restarted onto the patched code
- verify watcher/public-bridge health on the actual alpha machine
- verify builder workspace writes are confined to approved workspaces
- verify alpha operator knows the builder is bounded, not general autonomous engineering

## Readiness Read

Current read after Pass 2:

- the current alpha lane is now measured under real provider variability
- the measured lane is green
- the remaining risk is long-tail drift, not the previously known bot shell on the covered alpha path

Recommendation after Pass 2:

- invite-only alpha is reasonable now
- do one more hardening pass only if you want broader long-tail corpora or live multi-machine Hive soak before inviting real users
