# Mobile And Channel Rollout Plan

## Purpose

This document turns the mobile and channel direction into a concrete pre-Git rollout shape.

The goal is to support all three practical user-access paths:

- web companion,
- Telegram access,
- Discord access.

This is not a claim that all three are already polished products.

It is the intended controlled test order and system shape before wider sharing.

## Core Rule

All access surfaces must still route through the same NULLA core pipeline:

1. human input normalization,
2. task classification,
3. tiered context loading,
4. memory-first routing,
5. optional model execution,
6. candidate-versus-canonical knowledge separation,
7. response shaping,
8. local persistence and audit.

No channel or mobile surface is allowed to bypass this.

Current supporting internal boundaries:

- `core/channel_gateway.py`
- `core/mobile_companion_view.py`
- `ops/mobile_channel_preflight_report.py`

## Three Access Paths

### 1. Web Companion

Role:

- easiest phone-friendly interface,
- easiest place to show summaries and approvals,
- easiest place to expose "what NULLA knows" and "what was loaded."

Use for:

- mobile browser access,
- desktop quick access,
- summary panels,
- notification acknowledgment,
- approval flows,
- lightweight device status.

Why it matters:

- does not require native app packaging first,
- keeps UX under direct project control,
- and can become the safest first phone path.

### 2. Telegram Access

Role:

- fast conversational access path,
- good for remote prompts and notifications,
- good for lightweight interaction without full UI.

Use for:

- task submission,
- status checks,
- compact summaries,
- alert delivery,
- quick approval or deny actions.

Why it matters:

- users already understand it,
- cross-device reach is immediate,
- and the repo already has early Telegram relay groundwork.

### 3. Discord Access

Role:

- team and swarm coordination surface,
- useful for shared testing rooms,
- useful for lightweight operational visibility.

Use for:

- multi-user testing,
- shared updates,
- group coordination,
- and compact task or status relay.

Why it matters:

- friend-swarm testing often happens in Discord anyway,
- and it is a strong fit for controlled cross-device operator testing.

## Recommended Order

If all three are included in testing, the best order is:

1. web companion,
2. Telegram,
3. Discord.

Why:

- web companion gives the cleanest direct view into NULLA state,
- Telegram is the best first conversational phone surface,
- Discord is strong for team testing but less ideal as the first personal companion path.

## Device Role Mapping

### Primary Brain

Run on:

- desktop,
- laptop,
- stable server,
- or another reliable always-on machine.

Responsibilities:

- main memory,
- main task logic,
- local providers,
- archive and CAS access,
- swarm participation.

### Phone Companion

Run as:

- web companion access,
- Telegram access,
- Discord access,
- or later native/mobile wrapper.

Responsibilities:

- user interaction,
- summary viewing,
- presence mirror,
- lightweight metadata cache,
- approval prompts,
- notifications.

### Meet Infrastructure

Run only on:

- stable non-phone hosts.

Responsibilities:

- hot metadata,
- presence leases,
- holder and route indexing,
- snapshot and delta sync,
- regional federation support.

## Scope For The First Team Test

The first serious cross-device test should include:

- at least one primary NULLA brain per tester,
- at least one phone companion path per tester,
- at least one web companion session,
- at least one Telegram path,
- at least one Discord path,
- and meet nodes still running on stable non-phone hosts.

## What Each Surface Must Support

### Web Companion Must Support

- task send,
- response receive,
- summary view,
- recent activity view,
- compact presence and swarm view,
- and user-safe metadata visibility.

### Telegram Must Support

- inbound task messages,
- outbound compact responses,
- safe summary requests,
- and bounded alerts or approvals.

### Discord Must Support

- channel or webhook-based outbound updates,
- bounded inbound request handling if enabled,
- and separation between group coordination and private user context.

## Shared Safety Rules

All surfaces must respect:

- metadata-first behavior,
- local-first fallback,
- candidate knowledge isolation,
- provenance tagging,
- no blind raw history injection,
- and no automatic large remote fetch for mobile or channel output.

## What Must Not Happen

Do not let:

- phone surfaces become implicit meet nodes,
- Discord or Telegram bypass task routing,
- raw chat logs silently become canonical memory,
- mobile paths mirror full archive history by default,
- or channel output expose secrets or full internal traces.

## Success Condition

This rollout is successful when:

- a user can reach their NULLA from phone-friendly web, Telegram, and Discord surfaces,
- all three still go through the same core NULLA policy and memory flow,
- phone views stay lightweight and metadata-first,
- and the system remains usable if those surfaces go down.

## Post-Test Decision

After the controlled test phase, the project can decide whether the first real product surface should be:

- web companion first,
- Telegram-led companion first,
- a native mobile wrapper later,
- or a mixed approach.

That decision should happen after proof, not before.
