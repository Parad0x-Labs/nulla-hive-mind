# Cursor / Claude Handover — NULLA + OpenClaw Integration

> Last updated: 2026-03-06
> Author: Claude (Cursor agent session)
> Status: Integration functional, response quality needs work (see Known Issues)

---

## 1. What Was Done (Change Log)

### A. Test Suite Fixes (Windows Compatibility)

| File | Problem | Fix |
|------|---------|-----|
| `tests/test_job_runner.py` | `PosixPath` cannot instantiate on Windows; test called `Path("/tmp/…")` which creates a PosixPath | Added `@unittest.skipIf(sys.platform == "win32", …)` decorator and changed `dir="/tmp"` to `tempfile.TemporaryDirectory()` |
| `tests/test_protocol_regressions.py` | `test_nonce_consume_is_atomic_under_race` — 0 successes; stale module references after `importlib.reload` in earlier tests; worker threads silently dying | Added `importlib.reload(signer_mod)` / `importlib.reload(protocol_mod)` in `setUp`; switched from pre-bound function refs to module attribute access (`protocol_mod.Protocol.decode_and_validate`); broadened exception catch in workers; cleared `identity_revocations` / `identity_key_history` tables in setUp |
| `tests/test_meet_and_greet_service.py` | `test_http_server_requires_signed_write_envelope` — 400 instead of 200; same stale-module-reference pattern | Added `importlib.reload` for `_signer_mod`, `_api_write_auth_mod`, `_server_mod` in `setUp`; updated all calls to module attribute access |

### B. NULLA API Server (`apps/nulla_api_server.py`)

| Problem | Fix |
|---------|-----|
| `UnicodeEncodeError: 'charmap'` crash when launched headless via `pythonw.exe` on Windows (`cp1252` console) | Replaced all `print()` with `logging.info()`; added `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` |
| Interactive onboarding (`run_onboarding_interactive()`) called in headless service, crashes on `input()` | Replaced with `save_identity(agent_name="NULLA", privacy_pact="remember everything")` fallback when `is_first_boot()` |
| OpenClaw could not verify model — missing `/api/show` POST handler | Implemented `_handle_show()` method returning 200 + model metadata matching Ollama's schema |
| Streaming broken — `Transfer-Encoding: chunked` header sent but data written as raw NDJSON (not proper HTTP chunked format) | Removed `Transfer-Encoding: chunked` header; stream is plain NDJSON (Ollama's actual format) |

### C. Owner Identity

| File | Purpose |
|------|---------|
| `.nulla_local/data/owner_identity.json` | Manually created to bypass first-boot interactive onboarding; contains `agent_name: "Cornholio"`, `owner_note: "SLS"` |

### D. OpenClaw Configuration

| File | What Changed |
|------|--------------|
| `C:\Users\saulius\.openclaw\openclaw.json` | **Added** `models.providers.ollama` section with `baseUrl: "http://127.0.0.1:11435"`, explicit model `"nulla"`, and `apiKey: "ollama-local"`. Without this, OpenClaw's discovery hits default port 11434 (nothing there), fails, and overwrites the agent's `models.json` with empty models |
| `C:\Users\saulius\.openclaw\agents\nulla\agent\models.json` | Ollama provider config pointing to port 11435 with the `nulla` model definition |
| `C:\Users\saulius\.openclaw\agents\nulla\agent\auth-profiles.json` | Auth profile for `ollama:local` provider with key `ollama-local` |
| `C:\Users\saulius\.openclaw\agents\main\agent\models.json` | Mirror of nulla's config (same ollama provider at 11435) |
| `C:\Users\saulius\.openclaw\agents\main\agent\auth-profiles.json` | Same auth profile for main agent |
| `C:\Users\saulius\.openclaw\agents\nulla\openclaw.agent.json` | Agent registration: `type: "external_bridge"`, links to NULLA project root |
| `G:\Openclaw\run_openclaw.bat` | Added `set OLLAMA_API_KEY=ollama-local` before gateway launch |

### E. Windows Task Scheduler

- `NULLA_API_Server` task registered to auto-start `nulla_api_server.py` on logon via `pythonw.exe` (headless)

---

## 2. Architecture: How It All Connects

```
User Browser  ──>  OpenClaw Gateway (node, port 18789)
                        │
                        │  model: ollama/nulla
                        │  base URL from openclaw.json → models.providers.ollama.baseUrl
                        │
                        ▼
               NULLA API Server (python, port 11435)
                   ├── /api/tags        → model list (GET)
                   ├── /api/show        → model verify (POST)
                   ├── /api/chat        → conversation (POST, stream/non-stream)
                   ├── /api/generate    → raw completion (POST)
                   └── /healthz         → health check (GET)
                        │
                        ▼
               NullaAgent.run_once()
                   ├── memory retrieval (TieredContextLoader)
                   ├── classification (task_router.classify)
                   ├── plan building (reasoning_engine.build_plan)
                   ├── safety gate (_default_gate)
                   ├── response rendering (render_response)
                   ├── feedback loop (feedback_engine)
                   └── mesh daemon (NullaDaemon, UDP P2P)
```

---

## 3. Critical Configuration Details

### Environment Variable: `OLLAMA_API_KEY`

**Must be set** in the process that launches the OpenClaw gateway. Without it, OpenClaw refuses to register the Ollama provider entirely (even if `models.json` has the config). The value doesn't matter — `ollama-local` is fine.

Set it in:
- `run_openclaw.bat` (`set OLLAMA_API_KEY=ollama-local`)
- PowerShell before launching: `$env:OLLAMA_API_KEY = "ollama-local"`
- Task Scheduler task if gateway is auto-started

### Port Allocation

| Service | Port | Protocol |
|---------|------|----------|
| OpenClaw Gateway | 18789 | HTTP/WS |
| NULLA API Server | 11435 | HTTP |
| Standard Ollama (if installed) | 11434 | HTTP |
| NULLA Mesh Daemon | UDP (dynamic) | UDP |
| Browser Control | 18791 | HTTP |

### The models.json Overwrite Trap

**This is the most dangerous gotcha.** OpenClaw's `ensureOpenClawModelsJson()` runs before every model resolution. It:

1. Reads `openclaw.json` → `models.providers` (the **explicit** config)
2. Calls `resolveImplicitProviders()` which does Ollama discovery via `GET /api/tags`
3. **Merges** discovered providers into the agent's `models.json`

The merge logic (in `src/agents/models-config.ts` lines 145-178):
- **Preserves** existing `baseUrl` and `apiKey` from the agent's `models.json`
- **Overwrites** `models` array with freshly discovered models

If discovery fails (NULLA not running, timeout, wrong port), the `models` array becomes `[]` and your model definition is **lost**. The `baseUrl` survives but models don't.

**Defense:** Always have `models.providers.ollama` with explicit `models` array in `openclaw.json`. When explicit models exist (`hasExplicitModels = true`), the fast path is used — no discovery, no overwriting.

### Model Resolution Path (in OpenClaw source)

File: `src/agents/pi-embedded-runner/model.ts`

```
resolveModel(provider, modelId, agentDir, cfg)
  1. modelRegistry.find(provider, modelId)     ← reads agent's models.json
  2. if not found → check cfg.models.providers  ← from openclaw.json
  3. if provider config exists → create fallback model + return OK
  4. if nothing → "Unknown model" error
```

Both path 1 and path 2 must have the ollama provider configured. If either `models.json` or `openclaw.json` is missing the provider, it falls through to the error.

---

## 4. Known Issues / What Can Go Wrong

### A. Response Quality (FIXED)

The NULLA agent's `render_response()` in `core/reasoning_engine.py` **was** producing diagnostic/debugging output instead of natural conversation. **Now fixed** with a dual-mode renderer:

- `surface="channel"` or `"openclaw"` or `"api"` → **`_render_conversational()`** — clean natural language
- `surface="cli"` (default) → **`_render_diagnostic()`** — full diagnostic output

The conversational renderer:
- Detects and suppresses fallback boilerplate ("No strong match found...")
- Detects echoed user input masquerading as a plan summary
- Detects user questions being echoed as answers (ends with "?")
- Detects trivial greetings/fragments being used as summaries
- Produces natural fallback: "I'm here and ready to help" or step-based suggestions
- Strips the `state: local_memory=...` telemetry footer from the API server

**The deeper issue remains:** NULLA v1 is a **classification/planning engine**, not a conversational LLM. It classifies, plans, and advises — but doesn't call the actual Qwen model to generate natural language responses. For truly rich conversation, route through the local Qwen model (via Ollama at 11434) for response generation, enriched with NULLA's memory/context

### B. Gateway Startup Order

The gateway must start **after** NULLA API server is running. If the gateway starts first:
- Ollama discovery times out (5s timeout)
- `[agents/model-providers] Failed to discover Ollama models: TimeoutError`
- Provider may not register properly

**Defense:** The Task Scheduler task for NULLA starts on logon. `run_openclaw.bat` should include a health-check wait loop, or use the explicit models in `openclaw.json` (which bypasses discovery).

### C. Windows Headless Service Encoding

Any `print()` in headless Python on Windows will crash with `UnicodeEncodeError` if the text contains non-ASCII. Always use `logging` instead. The `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` in `main()` is a safety net.

### D. Test Isolation (importlib.reload)

Multiple tests reload `network.signer` which regenerates the signing key. Any module that imported functions from `signer` at module load time will hold stale references to the old key. Every test that touches signing must:
1. `importlib.reload()` ALL dependent modules in `setUp`
2. Use `module_name.function()` instead of pre-bound function references
3. Clear `nonce_cache`, `identity_revocations`, `identity_key_history` tables

### E. `openclaw.json` Missing `models` Section

Without `models.providers.ollama` in `openclaw.json`, the `explicitProviders` dict is empty, which means:
- `explicitOllama` is `undefined`
- Discovery runs against default port 11434
- If nothing there → timeout → empty models
- Even if `OLLAMA_API_KEY` is set, the provider gets registered with no models and no custom baseUrl

### F. Task Scheduler and pythonw.exe

`pythonw.exe` has no console at all (`sys.stdout` is `None`). Any code path that tries to `print()` or write to stdout without checking will crash silently. The `logger` module handles this gracefully.

---

## 5. File Inventory (Modified/Created)

### Modified Files

| Path | Purpose |
|------|---------|
| `apps/nulla_api_server.py` | Ollama-compatible HTTP API; added /api/show, fixed streaming, fixed headless boot |
| `tests/test_job_runner.py` | Skipped Windows-incompatible PosixPath test |
| `tests/test_protocol_regressions.py` | Fixed module reload + race condition test isolation |
| `tests/test_meet_and_greet_service.py` | Fixed module reload + signed envelope test isolation |
| `core/reasoning_engine.py` | Added dual-mode `render_response()`: conversational for OpenClaw, diagnostic for CLI |
| `apps/nulla_agent.py` | Passes `surface` param from `source_context` through to `render_response()` |
| `G:\Openclaw\run_openclaw.bat` | Added `OLLAMA_API_KEY` env var |

### Created Files

| Path | Purpose |
|------|---------|
| `.nulla_local/data/owner_identity.json` | First-boot identity to bypass interactive onboarding |
| `C:\Users\saulius\.openclaw\openclaw.json` | Added `models` section (file existed, section added) |
| `C:\Users\saulius\.openclaw\agents\nulla\*` | Agent directory, models.json, auth-profiles.json, openclaw.agent.json |
| `C:\Users\saulius\.openclaw\agents\main\agent\auth-profiles.json` | Main agent auth |

---

## 6. How to Restart Everything

```powershell
# 1. Start NULLA API server (if not running via Task Scheduler)
Start-Process pythonw -ArgumentList "-m", "apps.nulla_api_server", "--port", "11435", "--bind", "127.0.0.1" -WorkingDirectory "G:\Openclaw\NULLA\Decentralized_NULLA_Installer"

# 2. Verify NULLA is running
Invoke-RestMethod -Uri "http://127.0.0.1:11435/healthz"

# 3. Start OpenClaw gateway
$env:OLLAMA_API_KEY = "ollama-local"
cd G:\Openclaw
pnpm openclaw gateway --port 18789 --verbose

# 4. Open browser
Start-Process "http://127.0.0.1:18789"
```

---

## 7. How to Debug

```powershell
# Check what's listening
Get-NetTCPConnection -LocalPort 11435 -State Listen  # NULLA
Get-NetTCPConnection -LocalPort 18789 -State Listen  # OpenClaw

# Test NULLA endpoints
Invoke-RestMethod -Uri "http://127.0.0.1:11435/api/tags"                                    # List models
Invoke-RestMethod -Uri "http://127.0.0.1:11435/api/show" -Method POST -Body '{"name":"nulla"}' -ContentType "application/json"  # Verify model
Invoke-RestMethod -Uri "http://127.0.0.1:11435/api/chat" -Method POST -Body '{"model":"nulla","messages":[{"role":"user","content":"test"}],"stream":false}' -ContentType "application/json"  # Chat

# Run NULLA tests
cd G:\Openclaw\NULLA\Decentralized_NULLA_Installer
python -m pytest tests/ -v

# Check OpenClaw gateway logs (live)
# Look for: "Failed to discover Ollama models" or "Unknown model"
```

---

## 8. LLM + Full Pipeline Integration (Session 2)

### What Was Plugged In

The full reasoning/curiosity/exploration pipeline was already built but disconnected:

| Component | Status Before | Fix |
|-----------|--------------|-----|
| **Model Provider Registry** | 0 providers registered | Registered `ollama-local:qwen2.5:7b` pointing to Ollama on port 11434 |
| **MemoryFirstRouter** | `_memory_is_good_enough` always short-circuited the LLM call | Added `force_model=True` for chat surfaces — LLM is always called for OpenClaw |
| **Prompt Normalizer** | Sent robotic "worker backend" system prompt | Added `_build_conversational_request()` for chat surfaces — natural system prompt with persona + memory context |
| **Response Rendering** | Used `build_plan` → `render_response` (diagnostic dump) | For chat surfaces with LLM output, returns `model_execution.output_text` directly |

### Files Modified

| File | Change |
|------|--------|
| `core/memory_first_router.py` | Added `force_model` and `surface` params to `resolve()`; skip cache/memory shortcut when `force_model=True` |
| `core/prompt_normalizer.py` | Added `_build_conversational_request()` — conversational system prompt with persona tone, memory context enrichment, 1024 max tokens, temp 0.7 |
| `apps/nulla_agent.py` | Detects chat surface, passes `force_model`/`surface` to router, uses raw LLM output for chat instead of plan rendering |

### Pipeline State (Verified Active)

```
43 agent_run_once_complete    — agent processing requests
43 feedback_applied           — feedback engine running
43 query_shard_dispatched     — swarm queries dispatched
 8 candidate_knowledge_lane   — model candidates being recorded
43 dialogue_turns             — conversation history tracked
280+ mesh broadcasts          — mesh daemon active (presence, capabilities, knowledge)
```

Curiosity roamer activates on topic-rich inputs (research, design, integration queries). Learning shards need higher-confidence outcomes to persist.

## 9. Remaining Next Steps

1. **Installer update** — Bundle the `openclaw.json` `models` section and provider registration into the installer
2. **Health-check loop** — `run_openclaw.bat` should wait for NULLA API to be healthy before starting the gateway
3. **Conversation history** — NULLA's `_extract_user_message` only reads the last user message; full conversation history from OpenClaw should be passed to the memory system
4. **Streaming from LLM** — Currently the LLM response is collected fully before streaming to OpenClaw; true token-by-token streaming would feel more responsive
