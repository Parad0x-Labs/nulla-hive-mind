# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Nulla Hive Mind, **do not open a public issue.**

Instead, use one of these channels:

1. **GitHub Security Advisories** (preferred):
   https://github.com/Parad0x-Labs/nulla-hive-mind/security/advisories/new

2. **Email**: Reach out to the maintainers via the contact listed on the [Parad0x-Labs GitHub org](https://github.com/Parad0x-Labs).

We will acknowledge receipt within 72 hours and aim to provide a fix or mitigation plan within 14 days.

## Scope

### In scope

- The Brain Hive Watch server (`apps/brain_hive_watch_server.py`)
- API endpoints exposed by the Nulla runtime
- Input validation and sanitization in any public-facing route
- Authentication and authorization logic
- Dependency vulnerabilities

### Out of scope

- Vulnerabilities in third-party services (Ollama, OpenClaw) that are not caused by our integration
- Social engineering attacks
- Denial-of-service attacks against individual operator deployments
- Issues in forks or modified versions of this code

## Supported Versions

Only the latest `main` branch is actively maintained. We do not backport fixes to older tags or branches.

## Disclosure

We follow coordinated disclosure. We will credit reporters (unless they prefer to remain anonymous) when the fix is released.
