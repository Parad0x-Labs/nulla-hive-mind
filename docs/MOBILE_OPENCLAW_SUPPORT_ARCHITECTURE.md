# Mobile And OpenClaw Support Architecture

## Purpose

This document defines the intended role of phones and OpenClaw-style channel integrations in NULLA before public distribution.

The goal is not to turn mobile devices into full infrastructure nodes by default.

The goal is to let users:

- access their personal NULLA through familiar channels,
- keep a lightweight live view of presence and swarm state on phones,
- receive summaries and notifications on mobile,
- and participate in controlled team testing across devices before any Git-based distribution.

## Honest Current Truth

NULLA does not yet ship a native iOS or Android application.

What exists today is enough to justify the architecture:

- local-first NULLA agent flow,
- knowledge-presence metadata,
- meet-and-greet coordination scaffold,
- optional OpenClaw and Liquefy integration boundaries,
- a platform-neutral channel gateway scaffold,
- a metadata-first mobile companion snapshot layer,
- and early relay workers for Telegram and Discord.

This means mobile support is architecturally compatible with the current system, but still needs explicit companion-mode and channel-gateway proof before it should be described as a finished product path.

## Product Rule

Phones should be treated as lightweight companion clients first.

They should not be treated as the default place for:

- heavy local model execution,
- full archive storage,
- large CAS payload hosting,
- long-running meet-node authority,
- or canonical global truth.

The primary NULLA brain should usually remain on:

- a desktop,
- a laptop,
- a stable home server,
- or another reliable always-on machine.

## Mobile Roles

### 1. Companion Client

A phone may act as the user's personal NULLA front end.

Responsibilities:

- chat and task entry,
- response viewing,
- summary viewing,
- notification delivery,
- approval or deny actions for sensitive operations,
- and lightweight state introspection.

### 2. Presence Mirror

A phone may mirror hot metadata from the swarm.

Good mobile mirror data:

- current presence summary,
- knowledge holder summaries,
- replication counts,
- route hints,
- and recent finalized response summaries.

Bad mobile mirror data:

- full vault contents,
- large shard bodies,
- large trace bundles,
- full archive replay,
- or large remote payload caches by default.

### 3. Index Cache

A phone may hold a small cached subset of meet-and-greet and swarm-memory metadata for faster user views.

This should remain:

- bounded,
- metadata-first,
- privacy-filtered,
- and safe to invalidate.

### 4. Emergency Local Mode

A phone may eventually support a limited local-only mode for:

- quick summaries,
- local note capture,
- and offline queueing.

This should be treated as a convenience mode, not the default execution engine for the full system.

## OpenClaw Channel Role

OpenClaw-style integrations should be treated as channel and transport adapters.

They are a way to reach the user's NULLA, not a replacement for NULLA.

The system boundary should remain:

- NULLA owns memory,
- NULLA owns policy,
- NULLA owns persona,
- NULLA owns routing,
- NULLA owns validation,
- NULLA owns swarm logic,
- channels only carry user interaction.

## Supported Channel Shape

The intended first shape is:

- Telegram or Discord or similar channel receives user input,
- channel gateway normalizes the message,
- NULLA human-input adaptation runs,
- task classification runs,
- tiered context loading runs,
- memory-first routing runs,
- optional provider execution runs,
- candidate knowledge stays separated from canonical memory,
- response is shaped for the channel,
- and a compact result is returned to the user.

This keeps all serious product logic inside NULLA.

## Device Role Policy

The system should use explicit device roles.

### Primary Brain Node

Use for:

- main memory,
- full local reasoning,
- archive and CAS access,
- local provider execution,
- and swarm participation.

### Mobile Companion Node

Use for:

- user interaction,
- lightweight summaries,
- presence mirror,
- index cache,
- approval prompts,
- and notification or alert handling.

### Meet Node

Use for:

- regional hot metadata,
- presence leases,
- holder metadata,
- deltas and snapshots,
- and coordination state.

Phones should not be default meet nodes.

## Data Rules For Phones

Phones should receive:

- summaries,
- digests,
- compact manifests,
- task status,
- trace references,
- and fetch hints.

Phones should not automatically receive:

- secrets,
- raw full prompt history,
- large shard bodies,
- full archive exports,
- or sensitive remote payloads.

The mobile path should stay metadata-first and opt-in for deeper fetches.

## Sync Rules

The mobile companion path should prefer:

- pull or short polling,
- bounded cache windows,
- compact summaries,
- and explicit fetch on demand.

Avoid on phones:

- large background sync,
- permanent high-churn replication,
- full hot-state mirroring,
- or long-running heavy sockets unless a platform runtime proves it reliable.

## Battery And Network Posture

Phone support has to respect mobile reality.

That means:

- low idle bandwidth,
- small bounded cache,
- graceful reconnect,
- minimal background wakeups,
- and no assumption that the phone is always on.

This is another reason the phone should be a companion and mirror, not a core authority node.

## Channel Gateway Rules

The channel gateway should:

- normalize inbound channel events into one internal NULLA message shape,
- preserve provenance about source platform and channel,
- preserve user identity mapping,
- enforce local policy before any external action,
- and format outbound responses to fit the platform.

It should not:

- bypass NULLA memory and policy,
- inject raw channel messages directly into swarm truth,
- or publish channel-originated claims as canonical knowledge without review.

## Privacy And Safety Rules

Mobile and channel support must preserve local-first privacy posture.

Rules:

- do not mirror full private history to phones by default,
- do not expose raw swarm payloads in channel outputs by default,
- do not auto-fetch large remote knowledge into a phone companion path,
- do not auto-promote model-generated or channel-derived content into canonical memory,
- and keep provenance visible for anything that originated from a channel integration.

## Best Initial Test Scope

Before Git distribution, team testing should include phones as controlled companion devices.

Recommended scope:

1. One primary desktop or laptop NULLA per tester.
2. One phone companion path per tester.
3. One or more channel surfaces through Telegram or Discord.
4. Meet-and-greet still hosted on stable non-phone machines.

## Pre-Git Proof Items

Before claiming mobile or channel readiness, test at least:

1. Phone receives presence and summary updates without full payload sync.
2. User can send a task from phone or channel and receive a bounded response.
3. Channel messages flow through normal NULLA task classification and memory rules.
4. Sensitive local history is not dumped into the phone or channel path by default.
5. Phone reconnect after sleep or network change does not corrupt presence or user session state.
6. Phone companion cache can be dropped and rebuilt safely.
7. Telegram or Discord gateway outage does not damage the core NULLA runtime.

## What Is Not True Yet

Do not describe the current repo as having:

- a finished native mobile app,
- production-grade cross-platform background sync,
- production-grade push notification service,
- or public-ready universal channel support.

What is true is:

- the architecture supports those directions cleanly,
- the current relay and integration boundaries are compatible with them,
- and the system can be tested in that direction before Git distribution.

## Practical Rollout Order

1. Keep the main NULLA brain on desktop, laptop, or stable server hardware.
2. Use phones as companion clients and presence or summary mirrors.
3. Route Telegram and Discord through gateway-style integration, not direct product logic bypass.
4. Run team tests with phones included before broader sharing.
5. Only after that decide whether to build a native mobile app, a web companion, or both.

## Bottom Line

Mobile support is a good fit for NULLA if it stays:

- companion-first,
- metadata-first,
- local-brain anchored,
- and channel-gateway based.

That keeps the system usable and cheap without pretending a phone should be the whole swarm.
