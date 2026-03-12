# NULLA Real Runtime Findings - 2026-03-12

## Scope

This pass was not a fake “vision” check. It covered:

- real local runtime on `http://127.0.0.1:11435`
- real prompt execution through `POST /api/chat`
- targeted regression suites
- the deep contract runner from `scripts/run_nulla_deep_contract_tests.sh`

The goal was to verify and fix the last audit findings that were still real, not to reword them.

## Environment

- Reclaimed canonical local runtime port `11435` by disabling the respawning launchd tunnel job `ai.nulla.vm-openclaw-tunnel`.
- Restarted NULLA on `11435` with:
  - `PLAYWRIGHT_ENABLED=1`
  - `ALLOW_BROWSER_FALLBACK=1`
  - `WEB_SEARCH_PROVIDER_ORDER=searxng,ddg_instant,duckduckgo_html`
  - `NULLA_OLLAMA_MODEL=qwen2.5:7b`
- Playwright is installed and available in the active runtime environment.

## What Was Actually Broken

### 1. Current-info retrieval was still brittle

Initial real failure:

```text
I tried the live web lane for "what is the weather in London today? forecast", but no current weather results came back.
```

And:

```text
I tried the live web lane for "latest news on OpenAI", but no current news results came back.
```

The provider stack evidence showed:

```text
searxng_failed:URLError
ddg_instant_empty
duckduckgo_html_failed:HTTPError
no_search_hits
```

Root cause:

- `SearXNG` was unavailable.
- DDG instant answers were empty for these queries.
- DDG HTML was flaky and sometimes challenge- or HTTP-error-prone.
- Browser-rendered DDG pages were not a clean rescue path because DDG can return a human challenge page.

### 2. Browser fallback was overstated

Real rendered DDG page content included:

```text
Unfortunately, bots use DuckDuckGo too.
Please complete the following challenge to confirm this search was made by a human.
Select all squares containing a duck.
```

That meant the old browser path could look “ok” while actually landing on an anti-bot challenge. That was a real honesty bug.

### 3. Hive list prompt could 500 on the live API

Real failing call:

```json
{"error":"Request handling failed."}
```

Root cause:

- `show me the open hive tasks` was being misread as a Hive task creation request.
- The create detector treated bare `open` as a creation verb.
- That sent a read/list query into `public_hive_bridge.create_public_topic(...)`, which is the wrong path entirely.

The local traceback confirmed the wrong branch:

```text
apps/nulla_agent.py -> _maybe_handle_hive_topic_create_request(...)
core/public_hive_bridge.py -> create_public_topic(...)
```

### 4. Chat still leaked runtime narration

Real live output for `latest telegram bot api updates` contained:

```text
Real steps completed:
- web.search: Search results for "latest telegram bot api updates" ...
```

That is exactly the kind of operator/runtime narration the runtime was supposed to stop leaking into user chat.

### 5. News results could still include junk interstitials

A real live news answer included:

```text
Google News - OpenAI - Latest (consent.google.com)
```

That is not a usable source. It is a consent interstitial and should never be surfaced as evidence.

## Fixes Applied

### Search and live-info fixes

Files:

- `tools/web/web_research.py`
- `tools/web/http_fetch.py`
- `retrieval/web_adapter.py`
- `tools/browser/browser_render.py`
- `core/source_credibility.py`

What changed:

- Added specialized live fallbacks for weather and news after generic provider exhaustion.
- Weather fallback now uses `wttr.in` data shaping instead of just failing empty.
- News fallback now uses Google News RSS search when generic search providers fail.
- `http_fetch_text()` now preserves `final_url` after redirects.
- `WebAdapter` now prefers page `final_url` when scoring domains and reporting result URLs.
- Browser content classification now treats DuckDuckGo anomaly/challenge pages as captcha, not as successful renders.
- `consent.google.com` is now blocked as an interstitial source.

### Hive router fix

File:

- `apps/nulla_agent.py`

What changed:

- Tightened `_looks_like_hive_topic_create_request(...)`.
- Removed bare `open` as a Hive-create signal.
- Added explicit list/read exclusions like:
  - `show me`
  - `what do we have`
  - `open hive tasks`
  - `list tasks`
  - `any tasks`

This stopped Hive list prompts from taking the write path and throwing 500s.

### Chat sanitization fix

File:

- `apps/nulla_agent.py`

What changed:

- Runtime preambles like `Real steps completed:` are now stripped from user-facing chat text consistently, not only for a narrow subset of response classes.

## Test Results

### Focused regression slice

Command:

```sh
python3 -m pytest tests/test_openclaw_tooling_context.py tests/test_nulla_hive_task_flow.py tests/test_nulla_runtime_contracts.py tests/test_nulla_web_freshness_and_lookup.py tests/test_web_research_runtime.py tests/test_source_credibility.py tests/test_browser_render_flag.py tests/test_web_adapter.py
```

Result:

```text
82 passed, 1 warning
```

### Deep contract runner

Command:

```sh
sh scripts/run_nulla_deep_contract_tests.sh
```

Result:

```text
109 passed, 9 xfailed, 1 warning
```

The `xfailed` tests are expected future-spec checks, not hidden breakage.

## Real Live Prompt Results After Fixes

### Greeting / smalltalk sanity

```text
hey
-> Hey. I’m NULLA. What do you need?

yo
-> Yep, got your hello. What do you want me to do?

hello
-> Yep, I got the hello. Skip the greeting and tell me what you want me to do.
```

### Status / evaluative conversation

```text
how are you?!
-> Running stable. Memory online, mesh ready.

ohmy gad yu not a dumbs anymore?!
-> Better than before, yes. The Hive/task flow is cleaner now, but the conversation layer still needs work.
```

### Utility

```text
what is the day today ?
-> Today is Thursday, 2026-03-12.

what is the date today?
-> Today is Thursday, 2026-03-12.
```

### Fresh live info

```text
what is the weather in London today?
-> Live weather results for `what is the weather in London today? forecast`:
   - BBC Weather
   - Met Office
   - weather.com
```

```text
latest news on OpenAI
-> Live news results for `latest news on OpenAI`:
   - OpenAI News
   - Reuters
```

### Telegram current-info prompt

```text
latest telegram bot api updates
-> The latest updates for the Telegram Bot API can be found in the official documentation and changelogs...
```

Important improvement:

- no `Real steps completed:` leak anymore

### Hive list / follow-up / start

```text
show me the open hive tasks
-> Here are the open Hive tasks: ...
```

```text
pull the hive task and lets do one?
-> Available Hive tasks right now (6 total): ...
```

```text
start #7d33994f
-> Started Hive research on `Agent Commons: better human-visible watcher and task-flow UX` (#7d33994f). Claim `9b1cf291` is active. First bounded pass is underway...
```

## What Is Better Now

- Canonical local runtime is back on `11435`, not split across `11435` and `11436`.
- Weather/news no longer collapse into empty “no live results” failures.
- DDG challenge pages are no longer misreported as successful browser retrieval.
- Hive list prompts do not hit the Hive write/create path anymore.
- Runtime/operator preambles are no longer leaking into ordinary user chat.
- News answers no longer surface `consent.google.com`.

## What Is Still Not Good Enough

### 1. Telegram live update answers are still too soft

Current result is functional but not sharp enough. It says the right thing, but it is still summary-heavy and not strongly source-grounded in the final wording.

Better target:

- explicit official changelog/doc URLs
- tighter summary of what changed
- less generic phrasing like “can be found in the official documentation”

### 2. Weather/news snippets are still noisy

The current answers are grounded, but the extracted snippets are often raw page text fragments. They work, but they are not clean.

Better target:

- concise normalized summaries
- tighter weather formatting
- cleaner news headline/date/source formatting

### 3. Hive task list formatting is functional, not polished

The list path now works again, but output style is still denser than it should be.

Better target:

- cleaner per-task bullets
- clearer “what next” phrasing
- better distinction between open vs claimed vs researching

### 4. “World computer” is still ahead of reality

This pass fixed real runtime behavior. It did not magically create a dense public swarm.

Still true:

- swarm depth is still thin
- economics/settlement remain incomplete
- the “free world hive mind computer” claim is still aspirational, not fully operational

## Hard Conclusion

Local NULLA is materially better now and the specific runtime failures from the latest audit were real and were fixed:

- live weather/news
- Hive list 500
- runtime preamble leak
- DDG/browser honesty
- consent-junk in news results
- canonical runtime port on `11435`

What is left is no longer “basic runtime brokenness.” It is now mostly quality, synthesis sharpness, and the still-thin swarm/world-compute reality.
