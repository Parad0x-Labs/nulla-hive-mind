# Clean Runtime Soak Preparation

## Purpose

This document defines the safest preparation path before running an overnight soak.

The main rule is simple:

Do not run the soak in a dirty mixed-history runtime if you want trustworthy evidence.

## Correct Runtime Strategy

Use one of these:

### Best Option

Fresh runtime home

Meaning:

- new runtime directory,
- new DB,
- new key path,
- new temp and log path,
- no old task or event history mixed in.

### Acceptable Option

Sanitized runtime state

Meaning:

- old DB and runtime artifacts are archived or moved away,
- old runtime logs are archived,
- stale temp files are cleared,
- and only the intended clean runtime state remains active.

Do not blindly delete history if you may need it later.

Archive or move it first.

## Current Repo Truth

The repo already supports a separate runtime home through `NULLA_HOME`.

Runtime state should live under that runtime home, not inside the source tree.

## Morning Preparation Order

1. Freeze code and config.
2. Archive or move old mixed runtime artifacts.
3. Create a fresh runtime home for the soak.
4. Point NULLA to that runtime home.
5. Re-run the overnight readiness gate.
6. Start the real soak only after the readiness report is acceptable.
7. Run the morning-after audit against the same runtime home.

## What Must Resolve Into The Fresh Runtime

Make sure these resolve into the fresh runtime:

- database path,
- key path,
- temp path,
- logs path,
- and any other mutable runtime artifacts.

## Readiness Gate Expectation

Before starting the soak, run the overnight readiness report on the fresh runtime.

What you want:

- ideally `GO`,
- or at worst `GO_WITH_WARNINGS` for non-critical understood issues.

What you do not want:

- warnings caused only by stale historical junk,
- or `NO_GO`.

## Why This Matters

If you run the soak in a dirty runtime:

- stale task states can look like fresh failures,
- old event-chain problems can pollute the new run,
- old holder or lease rows can confuse knowledge-state review,
- and the morning-after audit becomes harder to trust.

The cleaner the runtime baseline, the more meaningful the soak evidence will be.

## Good Success Definition

A good soak run tomorrow means:

- clean or acceptably warned readiness gate,
- no illegal stuck-state buildup,
- clean event chain,
- coherent knowledge leases and holder maps,
- no runaway logs or temp growth,
- and a morning-after audit that is understandable and mostly clean.

## Bottom Line

The codebase is ready for a real soak run.

The remaining practical risk is runtime contamination, not missing architecture.

So tomorrow's real rule is:

Use a fresh runtime home, then trust the soak results.
