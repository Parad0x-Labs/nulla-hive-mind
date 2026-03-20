# Contributing to NULLA Hive Mind

NULLA is one system, not a pile of adjacent projects.

The center of gravity is:

`local runtime -> memory + tools -> optional trusted helpers -> visible proof`

If you contribute here, optimize for that spine instead of expanding side paths.

## Read This First

Do not start by free-roaming the repo.

Read in this order:

1. [`README.md`](README.md)
2. [`docs/SYSTEM_SPINE.md`](docs/SYSTEM_SPINE.md)
3. [`docs/STATUS.md`](docs/STATUS.md)
4. [`docs/CONTROL_PLANE.md`](docs/CONTROL_PLANE.md)
5. [`docs/PLATFORM_REFACTOR_PLAN.md`](docs/PLATFORM_REFACTOR_PLAN.md)
6. [`docs/PROOF_PATH.md`](docs/PROOF_PATH.md)

## Fast Repo Map

- `apps/`: runtime and service entrypoints
- `core/`: the real product logic
- `tests/`: the regression safety net
- `docs/`: source-of-truth docs
- `installer/`: one-click install/bootstrap

Ignore archived handovers and historical root docs unless you are specifically tracing old decisions.

## How to contribute

1. **Fork** the repository.
2. Create a feature branch from `main`.
3. Keep the change scoped to one real improvement.
4. Run cumulative regression for the scope you touched.
5. Open a pull request against `main`.

## Rules

- **All changes go through pull requests.** Direct pushes to `main` are blocked.
- **CI must pass** (lint + tests + build) before a PR can be merged.
- **At least one review** from a maintainer is required.
- **No secrets, API keys, SSH keys, private keys, or personal data** in any commit. If you accidentally commit one, tell us immediately.
- Keep PRs focused. One logical change per PR.
- If step 3 works but step 1 broke, the work is not done. Re-run the already-working scope cumulatively.
- Reduce ambiguity. If your change makes the repo look like more products instead of one clearer system, it is probably the wrong change.

## What you can work on

- Bug fixes
- Documentation improvements
- Public-web clarity and dashboard UI/UX suggestions
- Test coverage
- Installer improvements
- Proof-path and contributor-onboarding improvements

## What's out of scope for external PRs

- Changes to the live Brain Hive deployment infrastructure
- Operator-level configuration (that's per-instance)
- Anything that modifies the security boundary without prior discussion

## Code style

- Python: we use [ruff](https://docs.astral.sh/ruff/) for linting. Run `ruff check .` before submitting.
- No dead code or commented-out blocks.
- Tests live in `tests/` and use pytest.

## Test Discipline

This repo uses cumulative regression, not isolated green checks.

If your work has multiple steps, you must re-run the already-working scope every time you add a new step.

Good:

- step 1 passes
- step 1 + 2 pass
- step 1 + 2 + 3 pass

Bad:

- step 3 passes by itself
- earlier behavior silently regressed

Use [`docs/CUMULATIVE_STABILIZATION.md`](docs/CUMULATIVE_STABILIZATION.md) when your change crosses runtime, Hive, public-web, or proof surfaces.

## Communication

- **Discord**: https://discord.gg/WuqCDnyfZ8
- **Issues**: Use the issue templates for bugs and feature requests.
- **Security**: See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
