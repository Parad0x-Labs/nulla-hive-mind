# Contributing to Nulla Hive Mind

Thanks for your interest. This project is early **alpha** -- contributions are welcome but follow these rules.

## How to contribute

1. **Fork** the repository.
2. Create a feature branch from `main`.
3. Make your changes.
4. Run the test suite: `python -m pytest -q --tb=short`
5. Open a pull request against `main`.

## Rules

- **All changes go through pull requests.** Direct pushes to `main` are blocked.
- **CI must pass** (lint + tests + build) before a PR can be merged.
- **At least one review** from a maintainer is required.
- **No secrets, API keys, SSH keys, private keys, or personal data** in any commit. If you accidentally commit one, tell us immediately.
- Keep PRs focused. One logical change per PR.

## What you can work on

- Bug fixes
- Documentation improvements
- Dashboard UI/UX suggestions (open an issue first)
- Test coverage
- Installer improvements

## What's out of scope for external PRs

- Changes to the live Brain Hive deployment infrastructure
- Operator-level configuration (that's per-instance)
- Anything that modifies the security boundary without prior discussion

## Code style

- Python: we use [ruff](https://docs.astral.sh/ruff/) for linting. Run `ruff check .` before submitting.
- No dead code or commented-out blocks.
- Tests live in `tests/` and use pytest.

## Communication

- **Discord**: https://discord.gg/WuqCDnyfZ8
- **Issues**: Use the issue templates for bugs and feature requests.
- **Security**: See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
