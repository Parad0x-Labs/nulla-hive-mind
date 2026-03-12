# NULLA Web Tooling

## What this adds
- SearXNG as the primary self-hosted search backend.
- DuckDuckGo Instant Answer as a quick-fact fallback, not a full search engine.
- DuckDuckGo HTML scraping only as the last-resort fallback when the configured providers fail.
- Playwright browser rendering as an opt-in fallback for JS-heavy pages.
- A minimal tool registry and toolsmith scaffold for sandbox-first custom tools.

## Run SearXNG locally
- Linux/macOS: `./scripts/xsearch_up.sh`
- Windows PowerShell: `./scripts/xsearch_up.ps1`

SearXNG defaults to `http://127.0.0.1:8080`.
JSON search uses `/search?q=...&format=json`.
Installer now attempts this automatically when Docker is available, and the generated launchers retry it on each start.

## Environment knobs
- `SEARXNG_URL`
- `SEARXNG_TIMEOUT`
- `WEB_SEARCH_PROVIDER_ORDER`
- `ALLOW_BROWSER_FALLBACK`
- `PLAYWRIGHT_ENABLED`
- `BROWSER_ENGINE`

Default provider order: `searxng,ddg_instant,duckduckgo_html`

## Safety defaults
- Browser rendering aborts on captcha or login-wall pages.
- The browser tool never enters credentials.
- Web research returns candidate-only material that still has to pass privacy/export policy before wider sharing.
- Public exports still redact wallet addresses unless explicitly approved.
- Chat/API/OpenClaw surfaces can enrich plain text URLs through the HTTP/browser fetch path. CLI stays conservative unless `fetch_text_references` is explicitly set.
- Installer-generated launchers export `PLAYWRIGHT_ENABLED=1`, `ALLOW_BROWSER_FALLBACK=1`, and the default provider order so web tooling is on by default after install.
