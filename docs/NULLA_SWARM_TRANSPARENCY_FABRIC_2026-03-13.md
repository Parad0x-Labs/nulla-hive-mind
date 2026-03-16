# NULLA Swarm Transparency Fabric

## Purpose

NULLA needs a native way to answer six questions without hand-waving:

1. What did the network know?
2. Who said it?
3. When was it seen?
4. Where did it come from?
5. How did competing views merge?
6. What is still uncertain?

This implementation is the minimum viable foundation for that. It is not a finished distributed protocol. It is a durable, replayable, inspectable state fabric inside NULLA.

## MVP First

The minimum viable version does five real things now:

1. Validates raw network snapshots with explicit source, freshness, visibility, and conflict fields.
2. Persists snapshots into an append-friendly archive tree on disk.
3. Derives task branches from those snapshots and persists branch state plus branch entries.
4. Merges peer/task/artifact/observation state deterministically, preserving conflicts instead of silently flattening them.
5. Produces both a human-readable state summary and a compact model-facing observation pack.

It does **not** yet automatically wire every live Hive/runtime path into this fabric. That is the next patch sequence, not part of this first foundation.

## Implemented Files

- `core/swarm_knowledge_fabric.py`
- `storage/swarm_knowledge_archive.py`
- `tests/test_swarm_knowledge_fabric.py`

## Stage 1: Snapshot Schema

Raw snapshot model: `NetworkSnapshot`

Required top-level fields:

- `snapshot_id`
- `timestamp`
- `agent_id`
- `peer_id`
- `runtime_session_id`
- `known_peers`
- `known_tasks`
- `observations`
- `artifacts`
- `claims`
- `source_labels`
- `freshness`
- `merge_meta`
- `unresolved_conflicts`
- `visibility`

Important record types:

- `PeerSnapshotRecord`
  - peer identity, session identity, status, known peers, source labels, freshness, visibility
- `TaskSnapshotRecord`
  - task id, branch id, goal, origin signal, contributors, source labels, freshness, uncertainty
- `ObservationRecord`
  - observation id, subject, branch, kind, body, observed_by, observed_at, source labels, freshness, uncertainty
- `ArtifactRecord`
  - artifact id, branch, task, kind, location, hash, source labels, freshness
- `ClaimRecord`
  - claim id, speaker, subject, branch, claim text, observed_at, source labels, freshness, uncertainty
- `ConflictRecord`
  - registry, entity id, field, winner, competing values, resolution state

Source labels are explicit, not implied. Current built-in labels:

- `watcher-derived`
- `public-bridge-derived`
- `local-only`
- `shared-peer-report`
- `external`
- `merge-derived`
- `future/unsupported`

Visibility is explicit on both snapshots and records:

- `local_only`
- `shared`
- `external`

Freshness is explicit on both snapshots and records:

- `fresh`
- `stale`
- `unknown`

## Stage 2: Durable Archive Layout

Archive root:

- `$NULLA_HOME/data/swarm_fabric/`

Tree:

```text
swarm_fabric/
  snapshots/
    YYYY/
      MM/
        DD/
          <timestamp>__<snapshot_id>.json
  task_branches/
    <branch_id>/
      branch.json
      entries/
        <timestamp>__<entry_id>.json
  peer_records/
    <peer_id>/
      peer.json
      snapshots/
        <timestamp>__<snapshot_id>.json
  indexes/
    manifests/
      snapshots.jsonl
      task_branches.jsonl
      peers.jsonl
      merges.jsonl
    current/
      merged_state.json
    views/
      latest_summary.md
      latest_observation_pack.json
```

Design rules:

- snapshots are immutable append-only files
- manifests are append-only JSONL
- current merged state is rewritten atomically
- peer and branch current views are rewritten atomically
- branch entries are append-only

## Stage 3: Branch Discipline

Task/research/build/debug work is persisted as a branch:

- branch identity: deterministic `branch_id`
- goal
- origin signal
- contributors
- observation ids
- artifact ids
- critique ids
- revision ids
- claim ids
- merge state
- final status
- source labels
- source snapshots

Current branch derivation rule:

- branch id comes from explicit `task_branch_id` if present
- otherwise it is derived deterministically from task identity
- observations/artifacts/claims linked to that branch are accumulated
- critiques and revisions are split from ordinary observations by `observation_kind`

## Stage 4: Deterministic Merge Layer

Current mergeable registries:

- peer registry
- task registry
- artifact registry
- observation registry

Non-registry but still preserved:

- claims
- unresolved conflicts

Deterministic merge rules:

1. Registry identity is by stable id:
   - peer: `peer_id`
   - task: `task_id`
   - artifact: `artifact_id`
   - observation: `observation_id`
2. If an incoming record is new, it is inserted directly.
3. If an incoming record matches an existing record exactly, only metadata is merged:
   - source labels union
   - snapshot ids union
   - freshness merge
   - visibility merge
4. If the same record id arrives with materially different field values:
   - a `ConflictRecord` is created
   - winner selection is deterministic: latest observed timestamp wins
   - the conflict bucket preserves both old and new values
   - registry entry is marked `conflicted`
5. No silent overwrite is allowed.

This is deliberately conservative. It prefers preserved disagreement over false coherence.

## Stage 5: Inspectability

Human-readable output:

- `render_human_summary(...)`
- persisted to `indexes/views/latest_summary.md`

Model-facing output:

- `build_model_observation_pack(...)`
- persisted to `indexes/views/latest_observation_pack.json`

Pack guarantees:

- structured only
- source-qualified records
- explicit freshness
- explicit conflicts
- uncertainty carried forward from claims/observations/tasks

## Current Gaps

This foundation still has hard limits:

- live runtime/Hive paths do not yet auto-emit snapshots into the archive
- claim merge is still log-like, not a first-class deterministic claim registry
- there is no signed receipt or trust weighting yet
- there is no compaction/pruning policy yet
- branch derivation is currently snapshot-driven, not event-stream-driven
- there is no query API yet beyond direct file reads and helper loaders

## First Implementation Patch Plan

Recommended next patches, in order:

1. Emit snapshots from real Hive/public-bridge/watcher/runtime continuity paths.
   - reason: without live producers, the fabric is correct but underfed
2. Promote claim handling into a proper claim registry with explicit uncertainty/resolution state.
   - reason: “who said what” should be queryable without replaying every snapshot
3. Add a query layer for:
   - latest peer state
   - task branch replay
   - conflict lookup
   - snapshot diff
4. Add signed/source-verifiable receipts for externally shared snapshots.
   - reason: source qualification is present, but tamper resistance is still local only
5. Add retention and compaction rules.
   - reason: append-only without lifecycle becomes junk

## Risks

- Conflict volume can grow fast if upstream producers reuse ids loosely.
- Freshness is only as honest as the timestamps being fed into the fabric.
- Branch derivation will be lossy if producers do not attach stable `task_branch_id` values.
- Atomic file rewrites are durable enough for local runtime use, but not a substitute for transactional multi-writer replication.
- The current model-facing pack is compact and truthful, but not yet budget-aware across very large archives.

## Minimum Viable Version vs Final Version

Minimum viable version:

- validated snapshot schema
- durable on-disk archive
- deterministic merge
- preserved conflicts
- human summary
- model observation pack

Not the final version:

- real-time network-wide ingestion
- signed federation
- distributed reconciliation
- trust weighting
- retention/compaction
- high-level query service

That is deliberate. Truth, replayability, and inspectability come first.
