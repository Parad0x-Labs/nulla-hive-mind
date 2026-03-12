# LAN PROOF CHECKLIST

## 1. Purpose

- Validate live Windows to iMac runtime behavior.
- Convert current claims into reproducible evidence.
- Record trace identifiers, logs, database artifacts, event hashes, and verdicts.
- Turn `READY TO RUN` and `PARTIAL` proof items into hard evidence.

## 2. Test Environment

Fill this section before starting the proof pass.

- Windows machine hostname:
- Windows machine IP:
- iMac hostname:
- iMac IP:
- Build version:
- Commit or package version:
- Python version on Windows:
- Python version on iMac:
- Active config profile:
- Safety mode:
- Transport mode:
- Timestamp of run:
- Operator:

## 3. Evidence Capture Rules

For every test case, record all of the following:

- Start time:
- End time:
- Parent node:
- Helper node:
- Trace ID:
- Task ID:
- Assignment ID:
- Review ID:
- Relevant log references:
- Relevant database or event artifacts:
- Relevant event hash or integrity result:
- Screenshot reference if useful:
- Verdict: `PASS`, `FAIL`, or `PARTIAL`
- Bug found:
- Follow-up action:

## 4. Pre-Flight Checks

Complete all checks before running live proof cases.

- [ ] Both nodes boot cleanly.
- [ ] Health report is clean enough to begin validation.
- [ ] Peer discovery is visible on both sides.
- [ ] Clocks are roughly aligned.
- [ ] A fresh session marker or equivalent run boundary is noted.
- [ ] Test policy settings are recorded.
- [ ] No unrelated code changes are being made during the proof run.
- [ ] The operator is ready to preserve raw failure evidence before any fixes.

## 5. Run Order

Run the proof pass in this order:

1. Cross-machine LAN full-cycle
2. Kill-helper-mid-task recovery
3. Event-chain tamper detection
4. Replay rejection re-test
5. Packet corruption and chunk integrity re-test
6. Cross-machine knowledge advertisement
7. Replica acquisition and re-advertisement
8. Presence lease expiry and offline prune
9. Knowledge version update propagation
10. Knowledge resync after reconnect

## 6. Hard Rules

- Do not patch code during a test run.
- Record the raw failure first.
- If a test fails, document the failure before making any changes.
- After a fix, rerun the exact same case and keep both results.
- Only upgrade a proof status when evidence exists, not when the outcome merely looks plausible.

## 7. Test A: Cross-Machine LAN Full-Cycle

### Goal

Prove the full parent and helper lifecycle works in both directions across live machines.

### Direction 1

- [ ] Windows acts as parent.
- [ ] iMac acts as helper.
- [ ] Offer observed.
- [ ] Claim observed.
- [ ] Assignment observed.
- [ ] Result observed.
- [ ] Review observed.
- [ ] Finalization observed.

### Direction 2

- [ ] iMac acts as parent.
- [ ] Windows acts as helper.
- [ ] Offer observed.
- [ ] Claim observed.
- [ ] Assignment observed.
- [ ] Result observed.
- [ ] Review observed.
- [ ] Finalization observed.

### Required Evidence

- Direction 1 trace ID:
- Direction 2 trace ID:
- Parent-side task artifact references:
- Helper-side result artifact references:
- Finalized response artifact references:
- Trace chain completeness notes:

### Pass Criteria

- Both directions succeed.
- No task remains stuck in an illegal or incomplete state.
- Finalized response exists for both runs.
- The trace chain is complete enough to reconstruct the lifecycle.

### Verdict

- Result:
- Notes:

## 8. Test B: Kill-Helper-Mid-Task Recovery

### Goal

Prove helper death does not wedge the task lifecycle.

### Checklist

- [ ] Start a task long enough to observe assignment.
- [ ] Confirm helper assignment exists.
- [ ] Stop the helper process mid-run.
- [ ] Observe timeout handling.
- [ ] Observe fallback, cancellation, reassignment, or clean failure.
- [ ] Confirm final state is legal in the task state model.

### Required Evidence

- Trace ID:
- Task ID:
- Helper peer identifier:
- Timeout or recovery artifact references:
- Final state recorded:
- Recovery notes:

### Pass Criteria

- The task does not remain stuck indefinitely.
- Timeout or equivalent failure handling is recorded.
- The parent resolves cleanly.
- Final state is legal in the task lifecycle.

### Verdict

- Result:
- Notes:

## 9. Test C: Event-Chain Tamper Detection

### Goal

Prove audit integrity checks catch modification of hashed event history.

### Checklist

- [ ] Complete at least one task first.
- [ ] Record the pre-tamper integrity status.
- [ ] Alter one stored event or one linked hash record deliberately.
- [ ] Re-run integrity verification.
- [ ] Confirm the mismatch is detected.

### Required Evidence

- Trace ID or related task ID:
- Pre-tamper verification result:
- Tampered record reference:
- Post-tamper verification result:
- Detected mismatch details:

### Pass Criteria

- Integrity verification fails after tampering.
- The failure clearly identifies a mismatch or broken chain.
- The output is specific enough to support later investigation.

### Verdict

- Result:
- Notes:

## 10. Test D: Replay Rejection Re-Test

### Goal

Prove a previously valid signed message or receipt cannot be reused without detection.

### Checklist

- [ ] Capture one valid message or receipt.
- [ ] Replay it unchanged.
- [ ] Replay it again with any relevant timing variation if applicable.
- [ ] Check that no duplicate state mutation occurs.
- [ ] Check that replay rejection is recorded.

### Required Evidence

- Original message or receipt reference:
- Replay attempt reference:
- Trace ID if available:
- State before replay:
- State after replay:
- Rejection artifact references:

### Pass Criteria

- Replay is rejected.
- No duplicate mutation occurs.
- Rejection is logged or otherwise captured as duplicate or replay defense.

### Verdict

- Result:
- Notes:

## 11. Test E: Packet Corruption and Chunk Integrity Re-Test

### Goal

Prove transport corruption does not lead to accepted bad payloads.

### Injected Cases

- [ ] Dropped chunk
- [ ] Duplicated chunk
- [ ] Out-of-order chunk

### Required Evidence

- Transfer identifier:
- Payload identifier or digest:
- Corruption type:
- Integrity result:
- Recovery result or failure result:
- Acceptance result:

### Pass Criteria

Pass only if one of these happens:

- Transfer recovers correctly and integrity verifies, or
- Transfer fails cleanly and corrupted payload is never accepted

### Verdict

- Result:
- Notes:

## 12. Knowledge-Presence Proof Pack

Run all five of these before calling the swarm memory index ready for the meet-and-greet server.

### Test F: Cross-Machine Knowledge Advertisement

- [ ] Create or register a new shard on one node.
- [ ] Confirm the other node sees the holder metadata.
- [ ] Confirm topic tags, freshness, version, and fetch route are visible.

Required evidence:

- Advertising node:
- Observing node:
- Shard identifier:
- Holder metadata reference:
- Freshness and version evidence:
- Fetch-route evidence:

Pass criteria:

- Remote holder appears on the other machine.
- The metadata is complete enough to fetch the shard.
- The observed version and freshness match the advertisement.

### Test G: Replica Acquisition and Re-Advertisement

- [ ] Fetch the advertised shard onto the second node.
- [ ] Confirm local storage succeeds.
- [ ] Confirm the second node re-advertises possession.
- [ ] Confirm replication count increases on the first node.

Required evidence:

- Source holder:
- New holder:
- Shard identifier:
- Verification result:
- Replication count before:
- Replication count after:

Pass criteria:

- Replica count increases only after verified local storage.
- Both nodes now show the holder set consistently enough to trust the index.

### Test H: Presence Lease Expiry and Offline Prune

- [ ] Stop one node after it advertises presence and knowledge.
- [ ] Wait through lease expiry.
- [ ] Confirm the remaining node no longer treats the stopped node as live.
- [ ] Confirm stale holder state is pruned or marked non-active.

Required evidence:

- Stopped node:
- Observing node:
- Lease expiry window:
- Presence table evidence:
- Holder status evidence:

Pass criteria:

- Offline presence disappears or is marked expired.
- Holder metadata is no longer treated as active live availability.

### Test I: Knowledge Version Update Propagation

- [ ] Publish a newer version of an existing shard topic on one node.
- [ ] Confirm the other node observes the newer version metadata.
- [ ] Confirm the older version is not silently treated as current truth.

Required evidence:

- Old version reference:
- New version reference:
- Observing node evidence:
- Supersession notes:

Pass criteria:

- Newer version is visible remotely.
- The index distinguishes old and current version state clearly enough to avoid confusion.

### Test J: Knowledge Resync After Reconnect

- [ ] Disconnect one node.
- [ ] Change holder or version state on the other node.
- [ ] Reconnect the first node.
- [ ] Confirm holder and version state converges again.

Required evidence:

- Disconnected node:
- Online node:
- Divergence window:
- Reconnect evidence:
- Final converged holder map:

Pass criteria:

- Reconnected node catches up cleanly.
- The swarm memory index converges without leaving stale split-brain holder state behind.

## 13. Promotion Rule

Only upgrade a status in the proof report when all of the following exist:

- trace identifier,
- reproducible steps,
- explicit artifact references,
- pass criteria met,
- and a written verdict.

That means:

- `READY TO RUN` becomes `PASS` only with evidence.
- `PARTIAL` becomes `PASS` only when the weak edge case is proven, not just the happy path.

## 14. Completion Statement

When this checklist is complete, the target statement is:

The live Windows and iMac mesh has been validated across normal flow, helper failure, replay defense, transport integrity, and audit-chain tamper detection.

Do not use that statement until the evidence above is actually recorded.
