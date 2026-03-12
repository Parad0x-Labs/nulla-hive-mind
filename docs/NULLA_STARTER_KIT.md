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
- Task capsule scope controls (helpers remain non-executable by default).
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

## 5) Model Targeting (Qwen)

Current target profile for optional local model backend:

- Qwen instruct-style provider via OpenAI-compatible local HTTP runtime.
- sample model entry: `qwen2.5-7b-instruct`.

Important:

- Qwen is an adapter path, not a hard lock.
- NULLA remains model-agnostic.
- Provider manifests are optional and controlled by policy/registration.

Reference files:

- `config/model_providers.sample.json`
- `adapters/local_qwen_provider.py`
- `core/model_registry.py`

## 6) Installation and Launch (Fast Path)

Installer bundle output:

- `build/installer/Decentralized_NULLA_Installer_<timestamp>.zip`
- `build/installer/Decentralized_NULLA_Installer_<timestamp>.tar.gz`

Fast launchers inside extracted folder:

- Windows: `Install_And_Run_NULLA.bat`
- Linux/macOS: `Install_And_Run_NULLA.sh`
- macOS wrapper: `Install_And_Run_NULLA.command`

Guided installer launchers:

- Windows: `Install_NULLA.bat`
- Linux/macOS: `Install_NULLA.sh`
- macOS wrapper: `Install_NULLA.command`

Post-install runtime launchers:

- Windows: `Start_NULLA.bat`, `Talk_To_NULLA.bat`, `OpenClaw_NULLA.bat`
- Linux/macOS: `Start_NULLA.sh`, `Talk_To_NULLA.sh`, `OpenClaw_NULLA.sh`
- macOS wrappers: `Start_NULLA.command`, `Talk_To_NULLA.command`, `OpenClaw_NULLA.command`

`OpenClaw_NULLA.*` is the convenience launcher:
- starts NULLA API if needed,
- waits for readiness on `127.0.0.1:11435`,
- opens OpenClaw web UI.
- installer now creates a Desktop shortcut to this launcher for one-click daily start.

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

## 8) OpenClaw Bridge

Installer can generate an OpenClaw bridge folder with:

- `Start_NULLA.*`
- `Talk_To_NULLA.*`
- `openclaw.agent.json`
- `README_NULLA_BRIDGE.txt`

Default bridge paths:

- Linux/macOS: `~/.openclaw/agents/main/agent/nulla`
- Windows: `%USERPROFILE%\\.openclaw\\agents\\main\\agent\\nulla`

If your OpenClaw build supports external agent discovery in that folder, NULLA appears in side menu.
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

## 12) Safety and Credibility Guardrails

Current safety posture:

- Signed envelopes and replay checks are active.
- Helper capsules are constrained and non-executable by default.
- Knowledge stays candidate-first before promotion.
- Source credibility scoring and downranking exist for weak/propaganda sources.
- Human/social/media evidence should remain corroborated before trust elevation.

Operator rule:

- Do not represent current state as hostile-public production.
- Represent as strong closed-test alpha with real local and trusted mesh runtime.

## 13) Known Limits (Do Not Ignore)

- WAN adversarial resilience still incomplete.
- Full trustless economy not implemented.
- Public-scale DHT/relay hardening still open.
- Some advanced observability and release automation items still in progress.

## 14) Launch-Ready Hand-Off Summary

NULLA is ready for serious closed testing when run with clean runtime discipline and trusted-node boundaries.

Use this starter kit as the single front page.
Use deeper docs only when you need subsystem-level detail:

- `AGENT_HANDOVER.md`
- `docs/WHAT_WE_HAVE_NOW.md`
- `docs/IMPLEMENTATION_STATUS.md`
- `docs/TDL.md`
- `docs/NON_TECH_INSTALL_WALKTHROUGH.md`
