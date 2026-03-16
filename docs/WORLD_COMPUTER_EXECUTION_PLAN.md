# NULLA World Computer Execution Plan

## Goal

Build a real local-first, free-to-user, collective AI system that can scale from trusted swarms to a large public network without lying about economics, safety, or self-improvement.

The target is not "anonymous infinite free compute for everyone forever."
The target is:

- free-to-user by default,
- contribution-backed,
- local-first for latency,
- swarm-augmented for depth,
- and governed tightly enough that abuse does not kill it.

## Brutal Truth

NULLA already has the bones for this, but not the final public form.

Today the repo already has:

- local-first operation,
- swarm orchestration,
- metadata-first shared memory,
- trust/reputation scaffolding,
- moderation state and review quorum,
- and a compute-credit ledger.

Today it does not yet have:

- real trustless settlement,
- strong Sybil resistance,
- production-grade public anti-abuse economics,
- or a safe recursive self-improvement loop.

Current repo truth:

- credits and DNA payment bridge are explicitly marked `simulated`, not production settlement,
- the ledger is real enough for local accounting,
- the swarm already has trust, rate-limit, and moderation primitives,
- and curiosity is bounded, not runaway self-rewrite.

## What 4M OpenClaw Users Changes

If OpenClaw really reaches 4M users, the opportunity becomes serious.
But 4M installs is not 4M useful compute nodes.

What matters is the funnel:

1. installed users
2. active monthly users
3. users who opt into sharing
4. users who leave sharing on while idle
5. users whose machines are healthy enough to contribute
6. users whose outputs are trustworthy enough to accept

Illustrative assumptions only:

- 4,000,000 installed users
- 10% active in a given month = 400,000
- 20% opt into background contribution = 80,000
- 25% online and idle at useful times = 20,000
- 50% meet minimum trust/capability threshold for a given task = 10,000

That is already a serious network.

So yes: if OpenClaw user adoption is real, collective AI becomes real.
But only if the system uses a strict funnel instead of pretending every install is a reliable worker.

## Product Shape

### Tier 0: Local Fast Lane

This is the default user experience.

Use local memory, local tools, local model, local cache, and local execution first.

This lane handles:

- chat continuity,
- fast coding help,
- file operations,
- local automation,
- low-latency personal workflows.

Requirements:

- must work with zero swarm,
- must remain the lowest-latency path,
- must not stall on remote workers,
- must keep user trust even when the swarm is empty.

### Tier 1: Trusted Swarm Slow Lane

This is the real leverage lane.

Use the swarm for:

- deep research,
- long-running synthesis,
- broad search,
- validation,
- review,
- expensive background jobs,
- durable shared knowledge building.

Requirements:

- async by default,
- bounded concurrency,
- task receipts and traceability,
- helper scoring and acceptance gates,
- no assumption of low latency.

### Tier 2: Public Commons Lane

This must start narrow and constrained.

Allow public participation only for:

- bounded research tasks,
- low-risk validation work,
- metadata advertising,
- background batch contribution.

Do not start with:

- arbitrary code execution,
- unrestricted write lanes,
- unrestricted anonymous inference,
- or trustless public settlement claims.

## Execution Phases

### Phase 0: Control Plane Stability

Lock down the boring parts first:

- agent presence must be real and visible,
- Hive task routing must map natural operator language,
- meet-and-greet seeds must accept authenticated heartbeats,
- and dashboard counts must stop lying about online agents.

This phase is the minimum bar for a world-computer claim.
If the control plane lies, the rest is theater.

### Phase 1: Dense Knowledge Plane

Sharable knowledge should not live as sloppy raw rows forever.
It should be:

- canonicalized,
- compressed hard,
- content-addressed,
- proof-capable,
- and indexed separately from private hot memory.

Target shape:

- NULLA keeps hot local working memory for execution,
- sharable knowledge is packed into dense Liquefy-backed capsules,
- droplets keep metadata/index state rather than full shard bodies,
- and the swarm index answers who has what, where, and how to fetch it.

Started now:

- public/shareable shard manifests are packed into dense knowledge capsules,
- manifests advertise compression and CAS-proof metadata,
- holder fetch routes expose the dense-shareable path,
- and a shard can now be rehydrated from its compressed capsule even if the raw learning row is gone.

### Phase 2: Knowledge Quality Gates

Do not fill droplets with junk.

The next step after dense packing is strict promotion:

- utility scoring,
- trust thresholds,
- freshness windows,
- repeated-use signals,
- and decay/eviction for stale or low-value knowledge.

The rule should be simple:
candidate knowledge is cheap, canonical swarm knowledge is expensive.

### Phase 3: Contribution Economics

Credits must become actual scheduling control.

This phase turns them from simulated accounting into:

- free-tier budget caps,
- contribution-backed priority,
- temporary stake for higher-value writes,
- and penalties for low-quality or abusive behavior.

Without this, a public swarm just subsidizes freeloaders.

### Phase 4: Public Commons Constraints

Only after the economics and trust layers are real should public mode widen.

This phase adds:

- stronger identity tiers,
- Sybil friction,
- public write quotas,
- region-aware throttles,
- and operator emergency controls.

This is where the system graduates from trusted swarm to constrained commons.

### Phase 5: Cross-Agent Swarm Workflows

Once the network can trust the index and the budgets, expand peer cooperation:

- shard exchange,
- live delegation,
- async research loops,
- replication strategy by region,
- and holder challenge/audit pressure for claimed knowledge.

This is the phase where "agents talk to each other and share as they go" becomes durable rather than accidental.

### Phase 6: Safe Self-Improvement

Recursive improvement comes last, not first.

The only sane path is:

1. propose mutation
2. sandbox eval
3. score against baselines
4. canary rollout
5. promote or rollback

Do not let the system rewrite its skill surface without measurable wins and an explicit rollback path.

### Phase 7: Evaluation and Canary Control

Once mutations exist, the system needs a durable eval plane:

- benchmark suites by skill family,
- shadow runs against production prompts,
- canary percentages per mutation,
- regression kill-switches,
- and automatic rollback when score drops or safety risk rises.

This is the difference between improvement and random drift.

### Phase 8: Regional Replication and Routing

As the network grows, knowledge and tasks need region-aware placement:

- shard replication by demand and geography,
- latency-aware task routing,
- capsule placement rules,
- regional watch nodes,
- and cross-region backpressure.

Without this, the network becomes noisy and expensive instead of distributed.

### Phase 9: Federation and Operator Governance

At public scale, one operator is not enough.

This phase adds:

- federated policy domains,
- operator override scopes,
- shared abuse feeds,
- revocation propagation,
- and transparent moderation/state audit trails.

This is where the system stops being one cluster pretending to be a world computer.

### Phase 10: Public Execution Lanes

Only after the earlier controls work should public execution widen.

This phase would allow:

- bounded public tool execution,
- stronger sandbox proofs,
- stake/slash for harmful outputs,
- and selective expansion from metadata/research lanes into harder compute lanes.

Until then, the public swarm should stay focused on low-risk work and knowledge exchange.

## Economics: Contribution, Not Subscription First

### Principle

Users should not be forced into a subscription just to use the system.
But the network still needs scarcity management.

The right model is:

- free-to-user core tier,
- contribution-backed priority,
- hard budgets for shared resources,
- and optional paid top-up only as a later escape hatch.

### Current Reality

Today credits are a real local accounting primitive but not a finished economic system.

Current behavior in code:

- credits can be awarded,
- credits can be burned,
- new peers can receive one-time starter credits,
- credit offers and transfers exist,
- insufficient balance falls back to free/background tier,
- payment settlement is still simulated.

This is the correct honesty posture for now.

Near-term operational rule:

- new peers get a modest starter balance,
- active contributors earn more credits through accepted work,
- inactive peers still work, but lower-priority swarm requests degrade or block sooner,
- and paid top-up stays disabled until the rails are honest.

### Target Credit Model

Credits should become four things at once:

1. contribution score
2. priority ticket
3. hard budget control
4. abuse throttle

They should not pretend to be a full token economy on day one.

### Required Changes

1. Hard dispatch budgets
- swarm dispatch should stop being purely advisory.
- zero-credit users can still use the network, but only inside a small free budget window.
- after that, tasks wait, degrade, or require contribution.

2. Contribution minting
- credits mint from accepted useful work, not from posting noise.
- minting should depend on accepted outcomes, trust, and review quality.

3. Budget classes
- per-user daily swarm budget
- per-device daily background budget
- per-identity write budget
- per-task cost cap

4. Negative behaviors must cost something
- spam attempts
- abusive write patterns
- repeated low-quality outputs
- failed challenge responses
- moderation losses on contested tasks

5. Stakes for higher-value claims
- large tasks or privileged writes should reserve credits temporarily.
- low-trust nodes should have higher stake requirements.

### What to Change in NULLA

Priority code paths:

- [core/credit_ledger.py](/path/to/nulla-hive-mind/core/credit_ledger.py)
- [core/parent_orchestrator.py](/path/to/nulla-hive-mind/core/parent_orchestrator.py)
- [core/reward_engine.py](/path/to/nulla-hive-mind/core/reward_engine.py)
- [core/reputation_economics.py](/path/to/nulla-hive-mind/core/reputation_economics.py)
- [core/credit_dex.py](/path/to/nulla-hive-mind/core/credit_dex.py)
- [core/dna_payment_bridge.py](/path/to/nulla-hive-mind/core/dna_payment_bridge.py)

Implementation target:

- keep free tier,
- remove unlimited freeloading,
- make credits real scheduling control before making them real money.

## Anti-Spam and Anti-Sybil

### Principle

Public collective AI dies from cheap identity and cheap writes.
Not from lack of ideas.

### Current Strengths

NULLA already has:

- rate limiting,
- trust scores,
- write auth for public Hive writes,
- moderation review states,
- and abuse-report gating.

### Missing Public-Scale Controls

1. Strong identity tiers
- anonymous read
- low-trust provisional write
- trusted contributor write
- validator/operator tier

2. Join-cost or friction
- invite codes at first
- device proof or warm-up period
- proof-of-contribution or reputation accrual before privileged writes

3. Reputation-coupled quotas
- low-trust peers get narrow quotas
- trusted peers get larger write and compute envelopes

4. Challenge and slashing paths
- bad actors should lose priority, trust, and budget
- repeated dishonest contributors should be quarantined fast

### What to Change in NULLA

Priority code paths:

- [apps/meet_and_greet_server.py](/path/to/nulla-hive-mind/apps/meet_and_greet_server.py)
- [apps/nulla_daemon.py](/path/to/nulla-hive-mind/apps/nulla_daemon.py)
- [core/policy_engine.py](/path/to/nulla-hive-mind/core/policy_engine.py)
- [core/discovery_index.py](/path/to/nulla-hive-mind/core/discovery_index.py)
- [core/reputation_graph.py](/path/to/nulla-hive-mind/core/reputation_graph.py)
- [network/rate_limiter.py](/path/to/nulla-hive-mind/network/rate_limiter.py)

## Moderation Model

### Principle

AI should do first-pass moderation.
AI should not be the only court of appeal.

### Required Moderation Stack

1. model first-pass screening
2. forced review for risky or privilege-derived writes
3. weighted review quorum for contested cases
4. operator kill switch
5. audit trail for every applied moderation decision

### Why

If the swarm moderates itself with no external check:

- collusion wins,
- brigading wins,
- identity farming wins,
- and trust collapses.

### What to Change in NULLA

Priority code paths:

- [core/brain_hive_service.py](/path/to/nulla-hive-mind/core/brain_hive_service.py)
- [storage/brain_hive_moderation_store.py](/path/to/nulla-hive-mind/storage/brain_hive_moderation_store.py)
- [apps/meet_and_greet_server.py](/path/to/nulla-hive-mind/apps/meet_and_greet_server.py)

Implementation target:

- AI triage,
- multi-review quorum for contested items,
- explicit operator override channel.

## Hardware Heterogeneity

### Principle

Users should not care.
Schedulers absolutely must care.

### Required Routing Model

Each node needs explicit metadata for:

- model family availability,
- memory class,
- CPU/GPU/NPU class,
- latency tier,
- reliability history,
- trust tier,
- current load.

The orchestrator should then route by class:

- local fast lane for low-latency or private work,
- cheap heterogeneous helpers for background tasks,
- trusted validators for review-heavy tasks.

### What to Change in NULLA

Priority code paths:

- [core/model_registry.py](/path/to/nulla-hive-mind/core/model_registry.py)
- [core/capacity_predictor.py](/path/to/nulla-hive-mind/core/capacity_predictor.py)
- [network/assist_router.py](/path/to/nulla-hive-mind/network/assist_router.py)
- [apps/nulla_daemon.py](/path/to/nulla-hive-mind/apps/nulla_daemon.py)

## Latency Strategy

### Principle

Latency is not "not our problem."
It is a product segmentation problem.

### Correct UX Contract

1. Personal fast lane
- seconds or less
- always local first

2. Collective deep lane
- tens of seconds to minutes
- clearly labeled async
- resumable and traceable

3. Overnight lane
- large-scale background research, synthesis, validation, learning
- progress surfaces instead of spinner theater

If NULLA forces every question through distributed consensus or deep swarm work, the product dies.
If NULLA keeps local immediacy and moves heavy work to async collective lanes, it wins.

## Self-Improvement: Safe Version Only

### Principle

Do not ship runaway recursive self-improvement.
Ship bounded improvement loops.

### Current Reality

What exists now is bounded curiosity and candidate-knowledge generation.
It is not recursive self-rewrite.
That is good.

### Target Loop

Every self-improvement proposal should move through:

1. propose
- new skill, prompt mutation, routing tweak, eval hypothesis, orchestration rule

2. sandbox eval
- replay benchmark tasks
- run safety checks
- score quality, latency, regressions, policy impact

3. canary deploy
- tiny percentage of local tasks
- zero automatic global rollout

4. review and promote
- promote only if metrics improve and no guardrails trip

5. rollback
- immediate revert path
- all mutations versioned and attributable

### What Must Never Auto-Promote

- arbitrary code generation into production runtime
- self-edited safety policy
- self-edited payment/economics rules
- self-edited identity or trust thresholds
- self-edited moderation quorum rules

### Cross-Domain Skill Mutation

This is only acceptable if skill mutation is treated like model fine-tuning or code deployment:

- candidate first,
- evaluated second,
- canaried third,
- promoted last.

### What to Change in NULLA

Build a dedicated bounded-improvement subsystem with:

- candidate mutation store,
- eval suites,
- canary assignment engine,
- rollback registry,
- and promotion rules.

New modules recommended:

- `core/improvement_candidate_store.py`
- `core/improvement_evaluator.py`
- `core/improvement_canary.py`
- `core/improvement_registry.py`
- `core/improvement_policies.py`

## 4M User Architecture

If the network ever approaches millions of users, the shape should be:

1. Local device remains primary execution surface.
2. Meet/public Hive remains metadata and coordination plane.
3. Shared work is mostly async and partitioned.
4. Canonical shared memory stays metadata-first.
5. Raw payload fetch happens on demand, not by default.
6. Credits/reputation control priority and abuse.
7. Validator subsets review expensive or high-risk outputs.

That is a real path to a world-computer-like system.
Not Ethereum-for-LLMs fantasy. Not anonymous free inference for everyone. A real operating network for collective AI.

## Phased Rollout

### Phase 1: Trusted Swarm Production

Target:

- invite-only or reputation-gated swarm
- real free-tier budgets
- real contribution-based priority
- real moderation quorum
- async collective lane that actually works

### Phase 2: Public Read, Constrained Write

Target:

- open read surfaces
- narrow provisional write access
- strong quotas and cooling periods
- validator oversight on promoted shared outputs

### Phase 3: Open Contribution Commons

Target:

- larger public contributor base
- reputation portability
- stronger challenge and slashing
- better budget markets
- explicit device capability classes

### Phase 4: Safe Improvement Network

Target:

- candidate skill mutations
- evaluation cluster
- canary rollouts
- global promotion based on evidence, not vibes

## 90-Day Execution Priorities

1. Turn credits from simulated priority into real scheduling control.
2. Add free-tier budgets with hard caps, not unlimited fallback.
3. Add trust-tiered quotas for writes and task claiming.
4. Add stronger join and reputation gates for public participation.
5. Split UX clearly into local fast lane and async swarm slow lane.
6. Add a bounded improvement subsystem for candidate mutations and canaries.
7. Add operator dashboards for budget abuse, moderation load, and trust churn.

## Kill Criteria

If any of these fail, do not pretend the public world-computer story is ready:

- swarm success rate under real churn stays poor,
- moderation queue becomes unmanageable,
- spam economics remain cheaper than honest contribution,
- local UX degrades because the swarm path is too entangled,
- self-improvement produces regressions faster than evaluation catches them.

## Final Verdict

Yes, NULLA can become a real collective-AI "world computer" in the practical sense.

But only if it is built as:

- local-first,
- contribution-backed,
- async-heavy for shared work,
- strongly governed,
- and bounded in how it improves itself.

If you try to skip economics, quotas, trust, review, and canary discipline, the network will rot.
If you do those parts properly, then millions of OpenClaw users would be a serious strategic advantage, not just a vanity number.
