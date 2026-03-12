# Decentralized NULLA — 10/10 Roadmap

## Goal

Move from a strong closed-test prototype to a production-grade, defensible distributed AI system with:

- real model-backed helper reasoning,
- measurable runtime reliability,
- operator-grade observability,
- production deployment discipline,
- and a user-facing product surface that scales.

## Phase U0 (Immediate Closed-Test Quality)

1. Real helper execution quality:
- keep model-backed helper reasoning path enabled by default when providers exist,
- add speculative multi-path helper execution (N helper-model paths per capsule),
- add evaluator scoring for helper outputs and store it in review tables.

2. Operational truth:
- keep test-runtime isolation and clean migration bootstrap mandatory,
- keep implementation status and TDL synchronized with passing evidence.

## Phase U1 (Observability + Reliability Core)

1. Observability baseline:
- structured JSON logs on agent, daemon, meet node (done),
- meet request metrics endpoint (done),
- next: daemon/agent metrics surfaces and trace stitching.

2. Reliability controls:
- circuit breakers on provider and remote calls in all hot paths,
- bounded worker queues and backpressure in daemon execution loops,
- explicit graceful degradation modes (memory-only, local-only, read-only).

## Phase U2 (Deployment and Release Engineering)

1. Packaging and startup:
- maintain valid `pyproject.toml` packaging and script entry points,
- config schema validation with fail-fast startup errors.

2. Release process:
- CI gates: lint + tests + artifact build + release notes,
- versioned release manifests and staged rollout channels.

3. Secrets and runtime hygiene:
- env-first secret injection,
- key lifecycle and rotation guardrails,
- no live runtime state shipped in distributable folders.

## Phase U3 (Swarm-Grade Intelligence)

1. Distributed reasoning quality:
- helper memory augmentation before model call,
- structured reasoning payloads (`summary`, `steps`, `evidence`) with provenance,
- evaluator-weighted verdicting for non-deterministic tasks.

2. Trust and abuse hardening:
- signed claim verification across meet/hive writes,
- stronger holder-proof audits and anti-spam economics,
- adaptive Sybil-cost policy under abuse pressure.

## Phase U4 (User-Facing Product Surface)

1. Product UX:
- task progress timeline,
- knowledge browser with provenance,
- peer/network overview without doxing.

2. Companion/channel:
- stable channel gateway for Telegram/Discord/web,
- mobile companion status and summaries with metadata-first policy.

## Phase U5 (Moat Features)

1. Verifiability and trust:
- stronger computation verification artifacts,
- transparent trust graph and decision trail.

2. Swarm value network:
- knowledge marketplace primitives,
- reputation portability across devices and cluster regions.

## Current Baseline (2026-03-05)

- Helper path now supports model-backed reasoning with deterministic fallback.
- Meet now exposes runtime request metrics (`/metrics`, `/v1/metrics`).
- Structured logging is initialized on agent/daemon/meet-node startup.
- Test baseline currently: `188 passed, 5 skipped, 1 warning`.
