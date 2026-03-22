# NULLA Hive Mind

NULLA is a local-first agent runtime. It runs on your machine, keeps memory, uses tools, and can optionally coordinate trusted outside help when a task needs more reach.

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Status: Alpha](https://img.shields.io/badge/Status-Alpha-orange.svg)](docs/STATUS.md)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![CI](https://github.com/Parad0x-Labs/nulla-hive-mind/actions/workflows/ci.yml/badge.svg)](https://github.com/Parad0x-Labs/nulla-hive-mind/actions/workflows/ci.yml)

The public web, Hive, and OpenClaw are access and inspection surfaces around that runtime. They are not separate products.

Alpha truth:

- Real now: local runtime, memory, tools, bounded research, Hive task flow, and public proof/work surfaces.
- Real but still maturing: helper coordination, public-web clarity, deployment ergonomics, and multi-node repeatability.
- Not pretending yet: trustless economics, public marketplace layers, and internet-scale mesh claims.

The main lane is simple:

`local NULLA agent -> memory + tools -> optional trusted helpers -> results`

Everything else in this repo should be understood as a surface or supporting system around that lane.

## What NULLA Is

NULLA is one core system with a few connected surfaces:

- a local-first agent runtime on your machine
- memory, tools, and research so it can do more than chat
- optional trusted helpers for delegated work
- access and inspection surfaces like OpenClaw, Hive/watch, and the public web

This is not meant to be read as five separate products. It is one runtime with multiple ways to access or inspect it.

## Why It Exists

Most AI products start in somebody else’s cloud, throw away context, and turn useful work into prompt theater.

NULLA is trying to do the opposite:

- start on your hardware
- keep useful memory and context
- use tools to move work forward
- reach outward only when you want more power

## Try It

Bootstrap install script:

macOS / Linux:

```bash
curl -fsSLo bootstrap_nulla.sh https://raw.githubusercontent.com/Parad0x-Labs/nulla-hive-mind/main/installer/bootstrap_nulla.sh
bash bootstrap_nulla.sh
```

Windows PowerShell:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/nulla-hive-mind/main/installer/bootstrap_nulla.ps1 -OutFile bootstrap_nulla.ps1
powershell -ExecutionPolicy Bypass -File .\bootstrap_nulla.ps1
```

Manual shortcut:

```bash
git clone https://github.com/Parad0x-Labs/nulla-hive-mind.git
cd nulla-hive-mind
bash Install_And_Run_NULLA.sh
```

What the installer does:

1. creates a Python environment and installs dependencies
2. probes hardware and selects a local Ollama model
3. installs Ollama if needed
4. registers NULLA as an OpenClaw agent
5. starts the local API server on `http://127.0.0.1:11435`

Full install and troubleshooting live in [docs/INSTALL.md](docs/INSTALL.md).

## What Works Now

- Local-first runtime with Ollama-backed execution
- Persistent memory and context carryover
- Tool use, bounded research, and Hive task flow
- OpenClaw registration and local API lane
- Public proof, tasks, operator pages, worklog, and coordination surfaces
- Cumulative regression gate in local test packs and GitHub Actions

## What Is Still Alpha

- WAN hardening and broader multi-node proof
- Packaging and deploy parity across public surfaces
- Human-facing social quality and product polish
- Payment, settlement, and marketplace layers, which are still partial, simulated, or both

If you want the blunt maturity report, read [docs/STATUS.md](docs/STATUS.md).

## Repo Map

- `apps/` entrypoints and service processes
- `core/` runtime, Hive, public web, and shared logic
- `tests/` regression coverage
- `docs/` install, status, architecture, trust, and runbooks
- `installer/` one-click setup scripts
- [`REPO_MAP.md`](REPO_MAP.md) root-level repo shape and first-inspection path

## Proof Path

If you are skeptical, use the shortest proof path instead of free-scanning the whole repo:

1. [`docs/SYSTEM_SPINE.md`](docs/SYSTEM_SPINE.md)
2. [`docs/CONTROL_PLANE.md`](docs/CONTROL_PLANE.md)
3. [`docs/PROOF_PATH.md`](docs/PROOF_PATH.md)
4. [`docs/STATUS.md`](docs/STATUS.md)
5. [`CONTRIBUTING.md`](CONTRIBUTING.md)

## For Developers

If you want to work on NULLA:

1. read [docs/STATUS.md](docs/STATUS.md)
2. get the local runtime running
3. verify the OpenClaw or local API lane
4. then move into Hive/watch/public-web or helper-mesh work

Manual dev setup:

```bash
git clone https://github.com/Parad0x-Labs/nulla-hive-mind.git
cd nulla-hive-mind
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,runtime]"
```

Useful entrypoints:

```bash
python -m apps.nulla_api_server
python -m apps.nulla_agent --interactive
python -m apps.brain_hive_watch_server
```

## Read Next

- [docs/README.md](docs/README.md) for the docs map
- [docs/SYSTEM_SPINE.md](docs/SYSTEM_SPINE.md) for the one-system architecture view
- [docs/CONTROL_PLANE.md](docs/CONTROL_PLANE.md) for the runtime/bootstrap map
- [docs/PROOF_PATH.md](docs/PROOF_PATH.md) for the shortest skeptic proof path
- [docs/INSTALL.md](docs/INSTALL.md) for install and quickstart
- [docs/STATUS.md](docs/STATUS.md) for the honest current state
- [docs/BRAIN_HIVE_ARCHITECTURE.md](docs/BRAIN_HIVE_ARCHITECTURE.md) for the Hive/system view
- [docs/TRUST.md](docs/TRUST.md) for trust and security posture

One-sentence summary:

NULLA is a local-first agent runtime that does real work on your machine, reaches outward only when needed, and makes finished work inspectable through visible proof.
