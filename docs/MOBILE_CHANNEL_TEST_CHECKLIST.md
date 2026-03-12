# Mobile And Channel Test Checklist

## Purpose

Use this checklist during the first controlled pre-Git cross-device test phase.

This checklist is meant to prove four things at once:

1. phones can act as lightweight companion clients,
2. web companion access works as a sane first phone-friendly surface,
3. Telegram and Discord can act as access channels without bypassing NULLA,
4. mobile and channel paths preserve the same memory, policy, provenance, and privacy posture as the primary local runtime.

This is an operator-facing proof document, not a product pitch.

## Core Test Rule

Every mobile or channel request must still pass through the same core NULLA flow:

1. human input normalization,
2. task classification,
3. tiered context loading,
4. memory-first routing,
5. optional model execution,
6. candidate-versus-canonical knowledge isolation,
7. response shaping,
8. local persistence and audit.

If any surface bypasses that flow, the test fails even if the user-visible reply looks fine.

## Environment Matrix

Record this once per test run and keep it with the artifacts.

### Primary Brain

- host name
- platform
- role
- region
- active provider setup
- safety mode
- meet-node target

### Phone Companion

- device model
- platform
- network type
- battery state if relevant
- browser if using web companion

### Web Companion

- device used
- browser
- session identifier
- auth mode

### Telegram

- bot identity
- test chat or user identity
- ingress path used
- outbound path used

### Discord

- webhook or bot identity
- test channel identity
- ingress mode
- outbound mode

### Coordination Layer

- meet-node region used
- whether local-only or multi-node
- whether swarm metadata was available
- whether local models were available

## Evidence Capture Rules

For every test case record:

- start time
- end time
- operator
- user-facing surface
- request text
- interpreted request summary
- task ID
- trace ID if present
- session ID
- whether local memory hit occurred
- whether swarm metadata was consulted
- whether remote payload fetch happened
- whether cold context opened
- whether a model provider was called
- whether candidate knowledge was recorded
- whether the response was truncated for the channel
- whether sensitive history stayed excluded
- PASS / FAIL / PARTIAL
- notes

Preferred artifacts:

- screenshots
- copied response text
- prompt assembly report
- audit events
- relevant DB rows
- relevant meet-node or relay logs

## Global Preflight Gate

Do not start live device tests until all of these are true.

- [ ] Primary NULLA brain boots cleanly.
- [ ] Local summary view works.
- [ ] Prompt assembly reporting works.
- [ ] Meet-node or local coordination path is reachable if required for the scenario.
- [ ] Test workspace uses sanitized local runtime state.
- [ ] Channel credentials or safe local test adapters are configured.
- [ ] The phone can reach the intended access surface.
- [ ] Auth posture is known for every exposed service.
- [ ] Rate limits are known for every exposed service.
- [ ] Placeholder deployment values are not accidentally being used for the live test.

## Runtime Safety Gate

Confirm before each live surface test:

- [ ] Phone is not acting as a meet node.
- [ ] Phone is not acting as the primary archive host.
- [ ] Phone is not acting as the default heavy model host.
- [ ] Telegram or Discord are acting as optional access layers only.
- [ ] Outage of a channel surface would not stop the primary NULLA runtime.

## Session Isolation Gate

This matters because the same desktop brain may serve multiple channel users.

- [ ] Different Telegram users get different session scope.
- [ ] Different Discord users get different session scope.
- [ ] Web companion browser sessions do not collapse into one user history accidentally.
- [ ] Channel identity does not overwrite local desktop session identity.

## A. Web Companion Path

### Goal

Prove that a phone-friendly web path can act as the safest first mobile companion surface.

### Functional Checks

- [ ] Open the web companion on a phone.
- [ ] Confirm basic reachability and auth behavior.
- [ ] Send a normal task from the web companion.
- [ ] Receive a bounded response.
- [ ] Confirm the response still reflects NULLA task routing and memory rules.
- [ ] Confirm the task leaves normal task and audit state behind.

### State Visibility Checks

- [ ] Open a summary or "what NULLA knows" view.
- [ ] Confirm only bounded metadata and summaries are shown.
- [ ] Confirm recent tasks appear.
- [ ] Confirm recent finalized responses appear.
- [ ] Confirm presence or mesh state is compact, not raw.

### Privacy Checks

- [ ] Confirm no full archive is dumped by default.
- [ ] Confirm no raw private history is dumped by default.
- [ ] Confirm no large shard body is displayed by default.

### Pass Criteria

Pass only if:

- the web path works without bypassing the core pipeline,
- the response is coherent and bounded,
- and the view remains metadata-first.

## B. Telegram Path

### Goal

Prove that Telegram can act as a real access surface without becoming a bypass around NULLA logic.

### Functional Checks

- [ ] Send a task from Telegram.
- [ ] Confirm the task enters the standard NULLA flow.
- [ ] Confirm human-input normalization still applies.
- [ ] Confirm task classification still applies.
- [ ] Confirm tiered context loading still applies.
- [ ] Confirm memory-first routing still applies.
- [ ] Confirm candidate knowledge rules still apply if a model provider is involved.

### Response Checks

- [ ] Confirm the reply is compact and channel-appropriate.
- [ ] Confirm the reply is truncated safely if too long.
- [ ] Confirm response shaping does not expose internal-only detail accidentally.

### Privacy And Provenance Checks

- [ ] Confirm no large raw payload or private history is sent back by default.
- [ ] Confirm any channel-originated artifact remains provenance-tagged.
- [ ] Confirm Telegram-originated content does not become canonical truth by convenience.

### Pass Criteria

Pass only if:

- Telegram behaves like a front door,
- not like a bypass into ad hoc response generation,
- and private state remains bounded.

## C. Discord Path

### Goal

Prove that Discord can be used for team-testing access and updates without corrupting private user boundaries.

### Functional Checks

- [ ] Send a request through the Discord path if inbound is enabled.
- [ ] Confirm outbound updates can be delivered.
- [ ] Confirm shared-channel output stays bounded.
- [ ] Confirm Discord-originated input still passes through NULLA normalization and routing.

### Team-Safety Checks

- [ ] Confirm group-facing messages do not expose full private context.
- [ ] Confirm private user state is not dumped into a shared channel.
- [ ] Confirm Discord-originated data does not skip provenance tagging.

### Pass Criteria

Pass only if:

- Discord works as a bounded coordination or access layer,
- and shared-channel behavior does not weaken privacy or provenance.

## D. Phone Companion Behavior

### Goal

Prove that the phone acts like a useful lightweight companion rather than a hidden infrastructure node.

### Functional Checks

- [ ] Phone can view summaries and recent activity.
- [ ] Phone can view compact presence or swarm state.
- [ ] Phone can submit a bounded request by at least one supported surface.
- [ ] Phone can receive a bounded response.

### Resource And Scope Checks

- [ ] Phone does not become a meet node by default.
- [ ] Phone does not receive full shard bodies by default.
- [ ] Phone does not receive full archive exports by default.
- [ ] Phone does not silently enable heavy background sync.

### Reconnect Checks

- [ ] Phone can reconnect after sleep.
- [ ] Phone can reconnect after network change.
- [ ] Phone cache can be dropped and rebuilt safely.
- [ ] Phone reconnect does not corrupt presence or session state.

### Pass Criteria

Pass only if:

- the phone remains useful,
- lightweight,
- and metadata-first.

## E. Policy And Privacy Checks

### Goal

Prove that mobile and channel surfaces do not weaken NULLA privacy or memory discipline.

### Required Checks

- [ ] Sensitive local history stays excluded unless explicitly requested and allowed.
- [ ] Swarm metadata stays metadata-first.
- [ ] No large remote shard is auto-fetched only because the user used a phone or channel path.
- [ ] Candidate knowledge remains candidate only.
- [ ] Provenance stays visible for channel-originated content.
- [ ] Channel surfaces do not silently mirror private memory.
- [ ] Mobile surfaces do not silently open cold archive context.

### Pass Criteria

Pass only if:

- mobile and channel paths preserve the same safety posture as the local agent path.

## F. Failure Handling

### Goal

Prove that companion and channel failures do not damage the core runtime.

### Required Checks

- [ ] Telegram outage does not break the primary NULLA brain.
- [ ] Discord outage does not break the primary NULLA brain.
- [ ] Web companion disconnect does not corrupt task state.
- [ ] Phone reconnect does not create invalid presence state.
- [ ] Channel failure does not promote half-finished content into memory.
- [ ] Channel failure does not leave stuck task state forever.

### Pass Criteria

Pass only if:

- all mobile and channel paths remain optional access layers,
- and core NULLA behavior continues without them.

## G. Prompt And Context Discipline

### Goal

Prove that channel access does not reintroduce prompt bloat or unsafe context loading.

### Required Checks

- [ ] Bootstrap context remains bounded.
- [ ] Relevant context remains bounded.
- [ ] Cold context stays closed by default.
- [ ] Channel usage does not force full-history prompt assembly.
- [ ] Prompt assembly report shows what was included and excluded.
- [ ] Weak retrieval confidence is visible instead of hidden.

### Pass Criteria

Pass only if:

- mobile and channel requests still benefit from the tiered context loader rather than bypassing it.

## H. Model And Cost Discipline

### Goal

Prove that channel surfaces do not accidentally become the expensive path.

### Required Checks

- [ ] Memory hit still beats model call.
- [ ] Local model still beats paid fallback if configured.
- [ ] Paid fallback is not triggered only because the request came from Telegram, Discord, or phone.
- [ ] Provider output remains candidate knowledge only.

### Pass Criteria

Pass only if:

- the cost order remains memory first, local second, paid last.

## I. Rate Limit And Auth Checks

### Goal

Prove that the mobile and channel entry paths have known operational boundaries.

### Required Checks

- [ ] Auth mode is documented for each exposed surface.
- [ ] Rate limit behavior is known for each exposed surface.
- [ ] Oversized request behavior is known.
- [ ] Unauthenticated or malformed channel traffic is rejected safely where applicable.

### Pass Criteria

Pass only if:

- every externally reachable surface has explicit access posture instead of accidental exposure.

## J. Failure Injection Cases

Run these intentionally at least once.

- [ ] Telegram unavailable during request.
- [ ] Discord unavailable during outbound update.
- [ ] Phone loses connectivity mid-session.
- [ ] Web companion browser refresh during active task.
- [ ] Meet-node temporarily unavailable while phone companion remains open.
- [ ] Local model unavailable during a phone-initiated task.

Record:

- actual observed behavior
- whether task state remained legal
- whether user-facing messaging stayed sane

## K. Evidence Completion Gate

Do not upgrade the status of mobile or channel support based on gut feel.

Only mark a section complete when you have:

- reproducible steps,
- traceable task or session ID,
- captured response,
- captured artifacts,
- and explicit PASS criteria met.

## Promotion Rule

Do not describe mobile or channel support as ready until:

- web companion proof passes,
- Telegram proof passes,
- Discord proof passes,
- phone-companion proof passes,
- session-isolation proof passes,
- prompt-discipline proof passes,
- privacy or policy checks pass,
- and failure-injection checks pass.

## Final Statement

The target conclusion after this checklist is:

"NULLA can be accessed from phone-friendly web, Telegram, and Discord surfaces while preserving the same memory, policy, provenance, and local-first behavior as the primary core runtime."
