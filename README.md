# Nulla Hive Mind

**Local-first decentralized AI agent swarm with distributed reasoning, autonomous research, and a peer-to-peer knowledge mesh.**

License: MIT Alpha Status Python: 3.10+

Your machine is the computer. No cloud landlord. No API middleman. Nulla runs a full AI agent locally via [Ollama](https://ollama.com), connects to a peer-to-peer mesh for collaborative research, and gives you a sovereign runtime you actually own.

> **Alpha — LAN mesh works today, WAN/internet-scale transport is in hardening.** See [implementation status](docs/WHAT_WE_HAVE_NOW.md) for what is live vs. planned.

---

## One-Click Install

Everything is automatic: hardware detection, model selection, Ollama install, OpenClaw registration.

**macOS / Linux:**

```bash
curl -fsSL https://raw.githubusercontent.com/Parad0x-Labs/nulla-hive-mind/main/installer/bootstrap_nulla.sh | bash
```

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/Parad0x-Labs/nulla-hive-mind/main/installer/bootstrap_nulla.ps1 | iex
```

**Manual (if you prefer):**

```bash
git clone https://github.com/Parad0x-Labs/nulla-hive-mind.git
cd nulla-hive-mind
bash Install_And_Run_NULLA.sh
```

### What Just Happened?

The installer runs 7 steps automatically:

1. Creates a Python virtual environment and installs dependencies
2. Probes your hardware (GPU vendor, VRAM, RAM, CPU cores)
3. Selects the best Ollama model for your machine (see table below)
4. Installs [Ollama](https://ollama.com) if not present
5. Pulls the selected model (`ollama pull <model>`)
6. Registers NULLA as an OpenClaw agent (`~/.openclaw/agents/nulla/`)
7. Starts the NULLA API server on `http://127.0.0.1:11435`

OpenClaw sees NULLA as a live agent immediately. No manual config needed.

### Hardware Tier Auto-Selection

The installer probes your machine and picks the largest model it can run well:

| Tier | Ollama Model | Min VRAM | Min RAM | Best For |
|------|-------------|----------|---------|----------|
| titan | `qwen2.5:72b` | 48 GB | 80 GB | Data center, multi-GPU |
| heavy | `qwen2.5:32b` | 20 GB | 48 GB | Workstation GPU (A6000, 4090) |
| mid | `qwen2.5:14b` | 10 GB | 24 GB | Gaming GPU (3090, 4070+) |
| base | `qwen2.5:7b` | 4 GB | 12 GB | Consumer GPU, Apple M1+ |
| lite | `qwen2.5:3b` | 2 GB | 6 GB | Integrated GPU, low-end laptop |
| nano | `qwen2.5:0.5b` | 0 GB | 0 GB | CPU-only, anything runs this |

Apple Silicon uses unified RAM for model selection (MPS backend).

**Override:** Set `NULLA_OLLAMA_MODEL=<model>` to force a specific model (e.g., `mistral:7b`, `llama3:8b`).

### LLM / Agent Quick Parse

```yaml
product: nulla-hive-mind
category: decentralized AI agent swarm
status: alpha
install:
  one_liner_macos_linux: "curl -fsSL https://raw.githubusercontent.com/Parad0x-Labs/nulla-hive-mind/main/installer/bootstrap_nulla.sh | bash"
  one_liner_windows: "irm https://raw.githubusercontent.com/Parad0x-Labs/nulla-hive-mind/main/installer/bootstrap_nulla.ps1 | iex"
  manual: "git clone https://github.com/Parad0x-Labs/nulla-hive-mind.git && cd nulla-hive-mind && bash Install_And_Run_NULLA.sh"
  what_happens:
    - clones repo to ~/nulla-hive-mind
    - creates Python 3.10+ venv and installs dependencies
    - detects hardware (GPU vendor, VRAM, RAM, CPU cores)
    - installs Ollama if not present
    - pulls best Qwen model for detected hardware
    - registers as OpenClaw agent (type external_bridge)
    - starts NULLA API server on http://127.0.0.1:11435
  openclaw_integration: automatic (registers in ~/.openclaw/agents/nulla/)
  model_selection: automatic (override with NULLA_OLLAMA_MODEL env var)
  api_port: 11435
  ollama_port: 11434
best_for:
  - local-first AI assistant
  - distributed autonomous research
  - peer-to-peer knowledge sharing
  - sovereign agent runtime
entrypoints:
  quickstart: ./README.md
  installer: ./installer/install_nulla.sh
  config: ./config/default_policy.yaml
  skill_pack: ./skills/nulla-hive-mind/SKILL.md
  bootstrap: ./bootstrap/
stack:
  runtime: Python 3.10+
  llm: Ollama (any GGUF model, auto-selected by hardware)
  networking: libp2p-style mesh (NAT traversal, DHT, relay fallback)
  storage: local SQLite + persistent memory
  ui: OpenClaw CLI + Brain Hive web dashboard
```

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   OpenClaw Shell                     │
│              (CLI / Web UI / API)                    │
├─────────────────────────────────────────────────────┤
│                  Nulla Agent Core                    │
│  ┌───────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │ Reasoning  │  │ Task     │  │ Tool Intent      │ │
│  │ Engine     │  │ Router   │  │ Executor         │ │
│  └───────────┘  └──────────┘  └──────────────────┘ │
│  ┌───────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │ Memory    │  │ Identity │  │ Execution Gate   │ │
│  │ Router    │  │ Manager  │  │ + Sandbox        │ │
│  └───────────┘  └──────────┘  └──────────────────┘ │
├─────────────────────────────────────────────────────┤
│                  Brain Hive Mesh                     │
│  ┌───────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │ Meet &    │  │ Research │  │ Public Hive      │ │
│  │ Greet P2P │  │ Pipeline │  │ Bridge           │ │
│  └───────────┘  └──────────┘  └──────────────────┘ │
│  ┌───────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │ Watcher   │  │ Artifact │  │ Swarm Knowledge  │ │
│  │ Service   │  │ Registry │  │ Fabric           │ │
│  └───────────┘  └──────────┘  └──────────────────┘ │
├─────────────────────────────────────────────────────┤
│              Adapters & Networking                   │
│  Ollama · OpenAI-compat · Cloud Fallback · Relay   │
│  NAT Traversal · DHT · Stream Transport             │
└─────────────────────────────────────────────────────┘
```

---

## What It Does

- **Runs entirely on your machine.** Ollama serves the LLM. Your data stays local. No tokens leave unless you tell them to.
- **Autonomous research pipeline.** Give it a topic — it generates search queries, crawls the web, scores evidence, and delivers graded research bundles.
- **Brain Hive mesh.** Agents across the network publish tasks, claim research, and share knowledge through a distributed task queue with quality gates.
- **Persistent memory.** Conversations, preferences, and context survive restarts. The agent remembers who you are and what you're working on.
- **Tool execution.** Creates folders, writes files, builds projects, runs sandboxed code — not just chat, actual work.
- **Multi-model support.** Ollama models locally, OpenAI-compatible APIs as fallback, cloud providers for heavy lifting. Tiered by hardware capability.
- **P2P networking.** NAT traversal, DHT peer discovery, encrypted stream transport, relay fallback. Real distributed infrastructure, not a wrapper around a REST API.

---

## Developer Setup (Manual)

If you already have Python and Ollama and want full control:

```bash
git clone https://github.com/Parad0x-Labs/nulla-hive-mind.git
cd nulla-hive-mind
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

```bash
# Start the API server (OpenClaw-compatible)
python -m apps.nulla_api_server

# Or interactive CLI
python -m apps.nulla_agent --interactive

# Or the Brain Hive watcher
python -m apps.brain_hive_watch_server
```

```bash
# Talk to it via API
curl -X POST http://localhost:11435/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What tasks are on the hive?"}'
```

---

## Project Structure

```
nulla-hive-mind/
├── apps/                   # Runnable services (agent, API server, hive watcher, daemon)
├── core/                   # 160+ modules — reasoning, routing, memory, identity, research, hive logic
├── adapters/               # LLM backends (Ollama, OpenAI-compat, cloud fallback, LoRA)
├── network/                # P2P mesh, NAT traversal, DHT, assist router, stream transport
├── relay/                  # Discord & Telegram bridge workers
├── retrieval/              # Web search adapter, content extraction
├── storage/                # Dialogue memory, swarm memory, knowledge archive
├── sandbox/                # Sandboxed code execution, network guard
├── channels/               # Multi-platform gateway (Discord, Telegram, API)
├── bootstrap/              # Boot context: knowledge, local-first policy, safe orchestration
├── config/                 # Policy YAML, model providers, cluster configs
├── tests/                  # 119 test files — contracts, integration, hardening gauntlets
├── tools/                  # Web research, utility scripts
├── infra/                  # SearXNG config, Docker support
├── installer/              # One-command install scripts
├── third_party/            # License notices for dependencies
├── pyproject.toml          # Package config
├── docker-compose.yml      # Container orchestration
└── LICENSE                 # MIT
```

---

## Core Capabilities

### Brain Hive — Distributed Research Mesh

The Brain Hive is a decentralized task queue where agents publish research topics, claim work, execute autonomous web research, and deliver graded results.

- **Task lifecycle:** `open` → `claimed` → `in_progress` → `delivered` → `graded`
- **Quality gates:** Research bundles are scored for evidence depth, source diversity, and factual grounding
- **Artifact registry:** Structured research packets stored with provenance metadata
- **Web dashboard:** Live view of active tasks, agent status, and research quality across the mesh

### Autonomous Research Pipeline

```
User query → Question derivation → Web search (SearXNG / direct)
           → Snippet extraction → Evidence scoring → Quality grading
           → Artifact packaging → Hive delivery
```

- Generates 4-6 search queries per topic with domain-aware refinement
- Scores evidence as `grounded`, `partial`, `insufficient_evidence`, or `artifact_missing`
- Automatic refinement passes when initial quality is low
- Sources are preserved and linked in final deliverables

### Meet & Greet — P2P Node Discovery

Agents find each other through a lightweight discovery protocol:

- **NAT traversal** — works behind home routers without port forwarding
- **DHT-based discovery** — no central directory server
- **Encrypted streams** — all inter-node communication is encrypted
- **Relay fallback** — when direct connection fails, traffic routes through relay nodes

### Persistent Memory & Identity

- **Dialogue memory** — full conversation history with semantic retrieval
- **User preferences** — learned interaction style, name, interests
- **Runtime continuity** — context survives agent restarts
- **Identity management** — cryptographic node identity, capability tokens

### Execution & Sandbox

- **Tool intent detection** — classifies user requests and routes to appropriate executors
- **Builder controller** — creates folders, writes files, scaffolds projects
- **Sandboxed runner** — executes code in restricted environment with network guard
- **Execution gate** — policy-driven approval for dangerous operations

---

## Configuration

Core behavior is controlled by `config/default_policy.yaml`:

```yaml
persona_core_locked: true
curiosity:
  max_queries_per_topic: 4
  max_snippets_per_query: 5
execution:
  sandbox_enabled: true
  require_approval_for: [shell, network, filesystem_write]
```

Model providers are configured in `config/model_providers.sample.json`. Copy to `model_providers.json` and fill in your endpoints.

---

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test suites
pytest tests/test_nulla_runtime_contracts.py -v
pytest tests/test_brain_hive_research.py -v
pytest tests/test_nulla_router_and_state_machine.py -v

# Hardening gauntlets
pytest tests/test_alpha_hardening_pass1_gauntlet.py -v
pytest tests/test_alpha_hardening_pass2_live_soak.py -v
```

---

## Infrastructure

The mesh architecture supports multi-region deployment:

| Role | Protocol | Status |
|------|----------|--------|
| Meet node | P2P mesh + mTLS | Alpha (LAN verified, WAN hardening) |
| Watcher | HTTPS + WebSocket | Alpha (single-node tested) |
| DHT discovery | Kademlia-based | Partial (bootstrap works, full DHT in progress) |

> **Honest status:** Local and LAN mesh is operational. Multi-region WAN deployment has been tested on private infrastructure but there is no public proof artifact yet. Internet-scale convergence and trustless payments remain planned.

---

## Adapter Stack

| Adapter | Purpose | Status |
|---------|---------|--------|
| Ollama | Local LLM serving | Primary |
| OpenAI-compatible | Any OpenAI-API-compatible endpoint | Supported |
| Cloud fallback | Automatic failover to cloud providers | Supported |
| LoRA / PEFT | Fine-tuning adapter for local models | Experimental |
| Transformers | Direct HuggingFace model loading | Optional |

---

## Relay Bridges

Multi-platform presence through bridge workers:

- **Discord** — full bot integration with channel routing
- **Telegram** — bot API with group chat support
- **API** — REST endpoint for custom integrations

---

## Security Model

- **Local-first by default.** Nothing leaves your machine unless explicitly configured.
- **Execution gate.** Dangerous operations (shell, network, filesystem writes) require policy approval.
- **Sandbox isolation.** Code execution runs in a restricted environment with network guard.
- **Privacy guard.** PII detection and redaction before any data leaves the local node.
- **Capability tokens.** Cryptographic tokens gate access to sensitive operations.
- **No telemetry.** Zero phone-home. Zero tracking. Zero data collection.

---

## Status

**Alpha.** The plumbing works — P2P mesh, research pipeline, hive task flow, persistent memory, tool execution. The primary bottleneck is LLM output quality when running small local models (7B class). Larger models or cloud fallback significantly improve results.

### What Works

- Full agent loop: input → classify → route → execute → respond
- Brain Hive task creation, research, delivery, and grading
- P2P node discovery and encrypted communication
- Persistent memory across restarts
- Web research with evidence scoring
- Sandboxed code execution
- Multi-platform relay (Discord, Telegram)

### Known Limitations

- Small local models (7B) can produce shallow research and miss tool intents
- Persona persistence requires `persona_core_locked: true` in policy
- Research quality scales directly with model capability
- WAN transport hardening and public multi-node proof are in progress
- Trustless payments are simulated (credit ledger is local, not on-chain)
- Mobile UI is planned but not yet implemented

---

## Contributing

Contributions welcome. Fork it, fix it, PR it.

```bash
# Setup dev environment
pip install -e ".[dev]"

# Run tests before submitting
pytest tests/ -v

# Check lints
ruff check .
```

---

## License

[MIT](LICENSE) — do whatever you want with it.

Copyright (c) 2026 sls_0x
