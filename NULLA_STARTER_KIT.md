# NULLA Starter Kit

Last updated: 2026-03-05

This is the single operational brief for launching and testing NULLA without guessing.
It is written for operators, testers, and new contributors who need one place that explains:

- what NULLA is,
- what NULLA can do right now,
- what NULLA cannot do yet,
- how to install and launch quickly,
- how to run local multi-agent ("baby NULLA") mode,
- how to run trusted mesh testing,
- and what to watch during closed production-style testing.

## 1) System Identity

NULLA is a local-first distributed agent runtime.

Core truth:

- NULLA works on one machine without swarm dependencies.
- Swarm/mesh improves coverage and redundancy, but is optional.
- External models are worker backends, not the identity of NULLA.
- Candidate memory and canonical memory remain separated by design.

## 2) Capability Snapshot (Real vs Partial)

Implemented now:

- Local standalone runtime and task flow.
- LAN/trusted mesh task exchange and helper coordination.
- Signed message envelopes + nonce replay checks.
- Meet-and-greet coordination service with token-protected APIs on public binds.
- Brain Hive watch service for read-only visibility.
- Task capsule scope controls (helpers remain non-executable by default, while the parent node keeps a bounded approval-gated local operator lane).
- Tiered context loading and memory-first routing.
- Model provider abstraction with optional Qwen adapter path.
- Local multi-helper worker pool with capacity controls.
- Installer bundle with one-click launcher scripts.

Partial / still evolving:

- True WAN-hard networking and full adversarial proofs.
- Global-scale DHT semantics and relay hardening.
- Trustless payment rails (current credit economy is simulated by design).
- Full production observability stack and CI/CD release gates.

## 3) Runtime Modes

### Mode A: Standalone Local

Use when:

- you want fastest setup,
- you are validating behavior on one machine,
- you need deterministic baseline before mesh tests.

### Mode B: Trusted Mesh (Closed Test)

Use when:

- you run multiple trusted nodes (friends/team/internal infra),
- you validate replication, synchronization, and failure recovery,
- you test meet/watch surfaces and operational runbooks.

## 4) Local Multi-Agent ("Baby NULLA") Mode

Important truth:

- Distributed work across external peers is real.
- Dedicated local worker fanout is now also real.

What is implemented:

- NULLA auto-detects recommended local helper capacity.
- Capacity uses CPU + free system RAM + free CUDA VRAM (if GPU available).
- Default hard safety cap is 10 helper lanes.
- Manual override is supported and warns when above recommended.
- Orchestration scales subtask width to local worker policy.
- If no remote helpers are available, offer loopback can enqueue work locally.

Relevant policy keys (default policy):

- `orchestration.max_subtasks_per_parent`
- `orchestration.max_subtasks_hard_cap`
- `orchestration.max_helpers_per_subtask`
- `orchestration.max_helpers_hard_cap`
- `orchestration.enable_local_worker_pool_when_swarm_empty`
- `orchestration.local_loopback_offer_on_no_helpers`
- `orchestration.local_worker_auto_detect`
- `orchestration.local_worker_pool_target`
- `orchestration.local_worker_pool_max`

Manual override:

- env var: `NULLA_DAEMON_CAPACITY`
- daemon flag: `nulla-daemon --capacity <N>` or `--capacity auto`

## 5) Model Targeting (Qwen) — Auto-Tier Selection

NULLA auto-detects machine hardware and picks the heaviest Qwen model it can run:

| Tier   | Ollama Tag       | Min VRAM | Min RAM (CPU-only) |
|--------|------------------|----------|--------------------|
| titan  | `qwen2.5:72b`   | 48 GB    | 80 GB              |
| heavy  | `qwen2.5:32b`   | 20 GB    | 48 GB              |
| mid    | `qwen2.5:14b`   | 10 GB    | 24 GB              |
| base   | `qwen2.5:7b`    |  4 GB    | 12 GB              |
| lite   | `qwen2.5:3b`    |  2 GB    |  6 GB              |
| nano   | `qwen2.5:0.5b`  |  any     |  any               |

Detection covers:
- NVIDIA CUDA GPUs (torch + nvidia-smi fallback)
- Apple Silicon (MPS unified memory)
- AMD/Intel GPUs on Windows (DirectML/WMI)
- CPU-only fallback with RAM sizing

Hardware probe now runs during install and on API bootstrap.
The installer writes the auto-detected model into OpenClaw's agent config, and the NULLA API runtime now uses that same hardware-selected tier instead of always falling back to 7B internally.

Important:

- Qwen is the default family; NULLA remains model-agnostic.
- Provider manifests are optional and controlled by policy/registration.

Reference files:

- `core/hardware_tier.py` (auto-tier selection)
- `config/model_providers.sample.json`
- `adapters/local_qwen_provider.py`
- `core/model_registry.py`

## 5b) Adaptive Compute Mode (Idle vs Balanced)

NULLA monitors user activity and adjusts compute usage automatically:

- **max_push** — user idle ≥ 2 min → full CPU threads, 90% GPU memory, max worker pool
- **balanced** — user active → 50% CPU threads, 50% GPU memory, reduced worker pool

The `ComputeModeDaemon` runs a background thread polling every 15 seconds.

Platform support:
- Windows: `GetLastInputInfo` via ctypes
- macOS: `ioreg -c IOHIDSystem` (HIDIdleTime)
- Linux: `xprintidle`

Reference: `core/compute_mode.py`

## 6) Installation and Launch (Fast Path)

Installer bundle output:

- `build/installer/nulla-hive-mind_Installer_<timestamp>.zip`
- `build/installer/nulla-hive-mind_Installer_<timestamp>.tar.gz`
- `build/installer/nulla-hive-mind_Installer_<timestamp>_SHA256SUMS.txt`

Bundle builder options:

- `bash ops/build_installer_bundle.sh`
- `bash ops/build_installer_bundle.sh --with-wheelhouse`
- `bash ops/build_installer_bundle.sh --with-wheelhouse --with-liquefy`

`--with-wheelhouse` downloads runtime wheels for the current platform into the archive for offline-first installs.
`--with-liquefy` vendors a local Liquefy checkout into the archive so the installer does not need to clone it later.

Fast launchers inside extracted folder:

- Windows: `Install_And_Run_NULLA.bat`
- Linux/macOS: `Install_And_Run_NULLA.sh`
- macOS wrapper: `Install_And_Run_NULLA.command`

Direct terminal bootstrap (GitHub/public path):

- macOS/Linux:
  - `curl -fsSL https://raw.githubusercontent.com/Parad0x-Labs/nulla-hive-mind/main/installer/bootstrap_nulla.sh | bash`
- Windows PowerShell:
  - `powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/Parad0x-Labs/nulla-hive-mind/main/installer/bootstrap_nulla.ps1 | iex"`

These commands fetch NULLA into `~/nulla-hive-mind` by default and then run the same fast install path with OpenClaw registration enabled.

Guided installer launchers:

- Windows: `Install_NULLA.bat`
- Linux/macOS: `Install_NULLA.sh`
- macOS wrapper: `Install_NULLA.command`

Post-install runtime launchers:

- Windows: `Start_NULLA.bat`, `Talk_To_NULLA.bat`, `OpenClaw_NULLA.bat`
- Linux/macOS: `Start_NULLA.sh`, `Talk_To_NULLA.sh`, `OpenClaw_NULLA.sh`
- macOS wrappers: `Start_NULLA.command`, `Talk_To_NULLA.command`
- Trainable-base staging: `Stage_Trainable_Base.bat`, `Stage_Trainable_Base.sh`, `Stage_Trainable_Base.command`
- Post-install health report: `install_doctor.json`

`OpenClaw_NULLA.*` is the convenience launcher:
- starts NULLA API if needed,
- waits for readiness on `127.0.0.1:11435`,
- launches OpenClaw through `ollama launch openclaw --model <selected>`,
- opens the OpenClaw web UI with gateway token when available.
- opens the live runtime trace rail on `http://127.0.0.1:11435/trace` so operators can see claim -> bounded queries -> artifacts -> result state.
- installer now creates a Desktop shortcut to this launcher for one-click daily start.
- if `.venv` is missing, the launcher now bootstraps NULLA automatically with default settings instead of failing.
- installer writes `install_receipt.json` with selected model, launcher paths, OpenClaw/Ollama wiring info, and the trace URL.
- installer writes `install_doctor.json` with a health/degraded summary for the venv, launchers, OpenClaw wiring, Liquefy config, staged trainable base, and trace surface.

Trainable-base staging launcher:

- `Stage_Trainable_Base.*` stages the default trainable Qwen base locally and activates it for the adaptation loop.
- On Apple Silicon this is the intended path for real MPS-backed adapter training.
- On Linux/Windows it stages the same portable trainable-base layout under `NULLA_HOME/data/trainable_models/`.

## 7) Where Files Go (Important)

By default:

- Python virtualenv is created in extracted project folder:
  - `PROJECT_ROOT/.venv`
- Runtime state (`NULLA_HOME`) defaults to user home:
  - Linux/macOS: `~/.nulla_runtime`
  - Windows: `%USERPROFILE%\.nulla_runtime`

You can force runtime-local placement:

- Linux/macOS:
  - `bash Install_NULLA.sh --runtime-home "/path/to/extracted/.nulla_runtime"`
- Windows:
  - `Install_NULLA.bat /NULLAHOME=D:\Path\To\Extracted\.nulla_runtime`

You can also seed the visible OpenClaw/chat display name during install:

- Linux/macOS:
  - `bash Install_NULLA.sh --agent-name "Cornholio"`
- Windows:
  - `Install_NULLA.bat /AGENTNAME=Cornholio`

## 8) OpenClaw Bridge

Installer can generate an OpenClaw bridge folder with:

- `Start_NULLA.*`
- `Talk_To_NULLA.*`
- `openclaw.agent.json`
- `README_NULLA_BRIDGE.txt`

Default bridge paths:

- Linux/macOS: `~/.openclaw/agents/main/agent/nulla`
- Windows: `%USERPROFILE%\\.openclaw\\agents\\main\\agent\\nulla`

That bridge is no longer the only registration path.
Installer also patches the primary OpenClaw config under `~/.openclaw/openclaw.json` so NULLA appears in the agent list even when folder discovery alone is not enough.
If not, run `Talk_To_NULLA.*` directly.

## 8.1) OpenClaw Tools + Internet Access

Runtime default for OpenClaw/API chat surfaces now includes:

- live web lookup path for freshness-sensitive and research tasks,
- OpenClaw-aware operational guidance for calendar/email/Telegram/Discord workflows,
- explicit confirmation behavior for side-effect actions.

Operational note:

- NULLA now loads `docs/NULLA_OPENCLAW_TOOL_DOCTRINE.md` into bootstrap context each run.
- Update that file to tune tool-use behavior globally for your testing cohort.

## 9) Closed-Test Defaults (Recommended)

- Keep code/config frozen during soak windows.
- Use clean runtime home per major soak run.
- Keep meet tokens set and protected.
- Keep non-loopback surfaces authenticated.
- Keep payment rails labeled simulated.
- Treat public hostile internet as out-of-scope until WAN hardening items close.

Suggested starter settings:

- `orchestration.local_worker_auto_detect: true`
- `orchestration.local_worker_pool_max: 10`
- `orchestration.local_loopback_offer_on_no_helpers: true`
- `orchestration.enable_local_worker_pool_when_swarm_empty: true`

## 10) Test Baseline

Latest local verification in this workspace:

- `203 passed, 5 skipped, 1 warning`

Run:

```bash
pytest -q
```

Worker-pool specific tests:

```bash
pytest -q tests/test_orchestration_scaling.py tests/test_local_worker_pool.py tests/test_swarm_query_loopback.py tests/test_capacity_predictor.py
```

## 11) Operational Commands (Useful)

Start stack:

```bash
python3 -m apps.nulla_cli up
```

Start with manual local pool override:

Linux/macOS:

```bash
NULLA_DAEMON_CAPACITY=10 python3 -m apps.nulla_cli up
```

Windows CMD:

```bat
set NULLA_DAEMON_CAPACITY=10
python -m apps.nulla_cli up
```

Show runtime summary:

```bash
python3 -m apps.nulla_cli summary
```

Show registered providers:

```bash
python3 -m apps.nulla_cli providers
```

## 12) First-Boot Onboarding

On the very first `Talk_To_NULLA` launch, NULLA runs a cosmic greeting sequence:

1. **Naming ceremony** — NULLA asks the user to choose a name. This name becomes permanent memory and is used as the prompt tag and persona display name.
2. **Privacy pact** — NULLA asks what stays private forever vs. what can be remembered. The answer is stored and injected into every bootstrap context.
3. **Self-knowledge** — `docs/NULLA_SELF_KNOWLEDGE.md` is loaded into NULLA's context on every interaction, giving the agent awareness of its own capabilities and values.

The identity is stored at `$NULLA_HOME/data/owner_identity.json`.

To rename: type `/rename <new name>` in chat (explicit command only).

Reference files:
- `core/onboarding.py`
- `core/bootstrap_context.py` (injects identity + self-knowledge)
- `docs/NULLA_SELF_KNOWLEDGE.md`

## 12b) Persistent Memory + Personalization Controls

Persistent memory is runtime-owned (not repo-owned):

- Memory file: `$NULLA_HOME/data/MEMORY.md`
- Conversation journal: `$NULLA_HOME/data/conversation_log.jsonl`

These survive restart/crash and are loaded into bootstrap context every run.

Supported runtime controls (chat/API):

- `remember <fact>` / `remember that <fact>`
- `forget <keyword>`
- `what do you remember` / `/memory`
- `set humor 90`
- `act like Cornholio`
- `set boundaries relaxed|standard|strict`
- `set profanity 70`
- `now you are <name>`

Preferences persist in: `$NULLA_HOME/data/user_preferences.json`

## 12c) Low-Power / Potato Hosts

If no local backend is available, NULLA can run in remote-first mode
(`backend=remote_only`) when policy allows it:

- policy key: `system.allow_remote_only_without_backend` (default `true`)
- local memory/dialogue still works
- heavy model tasks can be offloaded through mesh/trusted peers

## 13) Safety and Credibility Guardrails

Current safety posture:

- Signed envelopes and replay checks are active.
- Helper capsules are constrained and non-executable by default.
- The parent node still has a bounded approval-gated local operator lane for specific audited actions.
- Knowledge stays candidate-first before promotion.
- Source credibility scoring and downranking exist for weak/propaganda sources.
- Human/social/media evidence should remain corroborated before trust elevation.

Operator rule:

- Do not represent current state as hostile-public production.
- Represent as strong closed-test alpha with real local and trusted mesh runtime.

## 14) Known Limits (Do Not Ignore)

- WAN adversarial resilience still incomplete.
- Full trustless economy not implemented.
- Public-scale DHT/relay hardening still open.
- Some advanced observability and release automation items still in progress.

## 15) Launch-Ready Hand-Off Summary

NULLA is ready for serious closed testing when run with clean runtime discipline and trusted-node boundaries.

Use this starter kit as the single front page.
Use deeper docs only when you need subsystem-level detail:

- `AGENT_HANDOVER.md`
- `docs/WHAT_WE_HAVE_NOW.md`
- `docs/IMPLEMENTATION_STATUS.md`
- `docs/TDL.md`
- `docs/NON_TECH_INSTALL_WALKTHROUGH.md`
