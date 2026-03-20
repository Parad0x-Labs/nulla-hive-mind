# NULLA Repo Map

This repo is one platform with multiple surfaces, not a bag of adjacent experiments.

Core lane:

`local runtime -> memory + tools -> optional trusted helpers -> visible proof`

## Canonical Root Files

- `README.md`: first product and developer entrypoint
- `REPO_MAP.md`: fast repo shape and where to look next
- `CONTRIBUTING.md`: contribution path and regression discipline
- `SECURITY.md`: security reporting
- `AGENT_HANDOVER.md`: redirect to current truth docs
- `NULLA_STARTER_KIT.md`: short operator quickstart
- `pyproject.toml`: package metadata and dependency entrypoint

## Root Directories

- `apps/`: public entrypoints and process launch surfaces
- `core/`: runtime, orchestration, policy, Hive, public-web, and shared logic
- `storage/`: persistence primitives and feature stores
- `tools/`: tool registry and built-in tools
- `network/`: transport, protocol, helper routing, and mesh boundaries
- `tests/`: regression, proof, and architecture smoke coverage
- `docs/`: current source-of-truth docs
- `installer/`: install/bootstrap UX and generated launcher paths
- `ops/`: deployment, hygiene, and operational automation
- `scripts/`: support utilities that are not product entrypoints
- `config/`: checked-in policy, cluster templates, and release metadata

## First 3-Minute Inspection Path

1. `README.md`
2. `docs/SYSTEM_SPINE.md`
3. `docs/CONTROL_PLANE.md`
4. `docs/PLATFORM_REFACTOR_PLAN.md`
5. `docs/PROOF_PATH.md`
6. `docs/STATUS.md`
7. `CONTRIBUTING.md`

## Package Maps

- `apps/README.md`: entrypoint ownership and thin-surface rule
- `core/README.md`: runtime/orchestration/public-surface ownership
- `storage/README.md`: persistence boundaries
- `tools/README.md`: explicit tool-contract boundary
- `network/README.md`: transport/auth/routing boundary

## What Lives At Root On Purpose

- Cross-platform launchers such as `Start_NULLA.*`, `Talk_To_NULLA.*`, and `OpenClaw_NULLA.*`
- Install entrypoints such as `Install_And_Run_NULLA.*` and `Install_NULLA.*`
- Workspace support files such as `AGENTS.md`, `SOUL.md`, `USER.md`, `TOOLS.md`, and `MEMORY.md`

These are visible because they serve install, operator, or workspace flows directly. Historical audits, handovers, and stray tests should not remain here.

## Archive Policy

- Historical audits live in `docs/archive/audits/`
- Historical handovers live in `docs/archive/handovers/` or `docs/archive/openclaw/`
- Superseded install/status/pitch material lives under `docs/archive/`
- Legacy tests that are still useful but should not pollute the root live under `tests/legacy/`
