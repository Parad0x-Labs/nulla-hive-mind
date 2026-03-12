# Cursor Audit Post-Fix Triage

**Date:** 2026-03-02  
**Scope:** Current local repository state after the audit-fix pass and federation pass  
**Mode:** Truthful triage, not a speculative read-only alarm report

## Executive Summary

Cursor's audit was directionally useful but overstated the current severity.

Several runtime faults it identified were real. Those have now been fixed.

Several other claims were either incorrect, overly severe, or framed as hard failures when they were actually:

- intentionally simulated behavior,
- non-active-path limitations,
- or product posture decisions rather than crashes.

The codebase is in a materially better state than the original audit described.

It is still not production-hardened public infrastructure.

It is now best described as:

- a working local-first NULLA runtime,
- a working LAN and early global-swarm coordination prototype,
- a region-aware meet-and-greet federation scaffold,
- and a system that still needs live multi-machine proof, release engineering, and public-exposure hardening.

## What Cursor Correctly Identified And Is Now Fixed

These were real issues and were addressed in the codebase:

- `apps/nulla_agent.py`
  Missing `find_local_candidates` and `rank` imports on the main agent path.
- `apps/nulla_daemon.py`
  Envelope handling used object-style access where decoded protocol messages are dict-like.
- `apps/nulla_daemon.py`
  Credit-related assist messages were not routed through the assist path.
- `core/assist_coordinator.py`
  Imported a non-existent `sign` symbol from `core.task_capsule`.
- `core/task_reassembler.py`
  Queried `tasks` instead of `local_tasks`.
- `core/finalizer.py`
  Used fallback `getattr(..., 1.0)` reads that produced fake perfect confidence/completeness.
- `core/parent_orchestrator.py`
  Used a dead ternary that always wrote the same completion state.
- `network/assist_router.py`
  Called `award_provider_score()` with the wrong signature on a live assist path.
- `network/protocol.py`
  Did not validate `CREDIT_OFFER` / `CREDIT_TRANSFER` payload structure.
- `network/protocol.py`
  Nonce cache growth was unbounded.
- `network/signer.py`
  Reloaded the local keypair from disk repeatedly.
- `storage/db.py`
  Re-opened SQLite connections constantly and duplicated schema-init logic.
- `retrieval/web_adapter.py`
  Stored a truncated query string as a pseudo-hash and mixed local naive timestamps into an otherwise UTC-shaped system.
- `apps/meet_and_greet_node.py`
  Sync-loop exceptions were swallowed instead of being recorded.

## What The Original Audit Got Wrong Or Overstated

These items should not be treated as current critical failures:

- `apps/nulla_node.py`
  The specific object-vs-dict crash claim was wrong. The file is still a stub-like path, but the cited failure mode was inaccurate.
- `apps/nulla_agent.py`
  The SQL placeholder mismatch claim was wrong. The statement shape was odd but not the crash Cursor described.
- `core/reward_engine.py`
  The weighting critique may still be a tuning discussion, but it was not a runtime “critical crash bug”.
- economy-related “dead system” claims
  Credits and payment flows are explicitly treated as simulated or scoreboard-first in current project posture. That is an honesty/roadmap issue, not a hidden runtime collapse.
- “reasoning is advisory” style findings
  Several items described safe current posture as if it were broken implementation. Some of that is intentional safety design.

## What Cursor Missed

The original audit also missed important issues that were real at the time:

- `apps/nulla_daemon.py`
  The live non-assist receive path had a more important envelope-access bug than the audit emphasized.
- `apps/nulla_daemon.py`
  Credit messages were being dropped before later routing stages because of the daemon choke point, not only because of downstream handler gaps.
- `tests/test_knowledge_presence.py`
  The suite was actually red during the earlier audit stage because of persistent knowledge-index state contamination.

Those issues were fixed during the same remediation pass.

## Additional Work Completed After The Audit Fix Pass

The repo moved beyond the original audit scope in two important ways:

### 1. Region-Aware Meet Federation

The meet-and-greet layer now carries region identity and summary behavior:

- presence records now carry `home_region` and `current_region`
- knowledge holders now carry `home_region`
- meet snapshots now distinguish:
  - `regional_detail`
  - `global_summary`
- the service now returns:
  - full in-region detail
  - summarized cross-region pointers
- replication now uses:
  - detailed delta/snapshot behavior inside a region
  - summarized snapshot behavior across regions

This is the first real step from “one global meet brain” toward a regional federation shape.

### 2. Global 3-Node Deployment Prep

The repo now includes a first deployment pack for the initial global test shape:

- Europe seed
- North America seed
- Asia-Pacific seed
- shared agent bootstrap sample
- cluster manifest and rollout notes

That is intended for the first real global test, not for mass public deployment.

## Current Remaining Real Risks

These are the real issues that still matter after the fix pass:

### Runtime / Proof Gaps

- live multi-machine proof still matters more than local tests
- cross-region convergence is not yet proven on a real 3-node deployment
- lease expiry and resync behavior still need live evidence on distributed hosts
- summarized cross-region routing needs real multi-host validation

### Security / Exposure Gaps

- meet-and-greet HTTP API still has no serious authn/authz model
- rate limiting and abuse controls are still not hardened
- WAN/public exposure remains partial and should not be described as finished
- hostile-public execution is still out of scope for the current sandbox posture

### Product / Platform Gaps

- release engineering is still incomplete
- update channels and version-compatibility policy are not yet productized
- payment/credit rails remain intentionally non-production and should stay that way for now
- full public-internet-scale decentralized-economy claims would still be overstated

## Current Verification State

Verification completed locally after the fix and federation passes:

- `32` automated tests pass
- regression coverage now includes:
  - protocol regressions
  - knowledge presence
  - meet-and-greet service behavior
  - meet-and-greet replication behavior
  - regional summary behavior
- import smoke for the previously broken runtime paths passed during the remediation pass

This means the current repository state is internally more coherent than the original audit report suggested.

It does **not** mean the system is ready for:

- hostile public exposure,
- public-money settlement,
- or production internet-scale rollout.

## Honest Current Verdict

The original Cursor audit was useful as a bug-hunt starting point.

It is no longer a truthful description of the current repo state.

The truthful post-fix position is:

- major active-path correctness failures from the audit were fixed
- the meet layer is now region-aware instead of globally flat
- the repository has a practical 3-node global test shape prepared
- the codebase is credible as a local-first and early-federated swarm prototype
- the remaining blockers are now mainly proof, hardening, and release engineering

## Recommended Next Gate

Before broader friend-to-friend sharing or public repo distribution, the next proof gate should be:

1. deploy the 3 regional meet nodes
2. run the live sync and failover proof pass
3. verify regional detail vs global summary behavior across real hosts
4. confirm version compatibility and bootstrap behavior for agent installs
5. only then move into release-readiness and GitHub distribution work
