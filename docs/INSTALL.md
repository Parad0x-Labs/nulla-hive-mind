# NULLA Install

This is the canonical install and quickstart doc.

## Fast Path

macOS / Linux:

```bash
curl -fsSLo bootstrap_nulla.sh https://raw.githubusercontent.com/Parad0x-Labs/nulla-hive-mind/main/installer/bootstrap_nulla.sh
bash bootstrap_nulla.sh
```

Windows PowerShell:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/nulla-hive-mind/main/installer/bootstrap_nulla.ps1 -OutFile bootstrap_nulla.ps1
powershell -ExecutionPolicy Bypass -File .\bootstrap_nulla.ps1
```

Probe the machine and provider reality before or after install:

```bash
bash Probe_NULLA_Stack.sh
```

```powershell
.\Probe_NULLA_Stack.bat
```

The probe reports:

1. machine hardware summary
2. installed Ollama models
3. whether the machine can reasonably run one local model or a primary/helper local pair
4. which remote provider credentials are actually configured
5. which provider stacks are real, not wired yet, or unsupported

Manual local shortcut:

```bash
git clone https://github.com/Parad0x-Labs/nulla-hive-mind.git
cd nulla-hive-mind
bash Install_And_Run_NULLA.sh
```

## What The Installer Does

1. creates a Python environment and installs dependencies
2. probes hardware and selects an Ollama model tier
3. installs Ollama if it is missing
4. pulls the selected local model
5. installs the OpenClaw bridge and registration path
6. starts the NULLA API server on `127.0.0.1:11435`
7. installs the `Probe_NULLA_Stack` command into the install root so the machine can be re-checked later without guesswork
8. on macOS, hands off the final launch to `OpenClaw_NULLA.command` so the running services are owned by Terminal.app instead of the short-lived installer shell

If you want the shortest user path, this is it.

If you already have a verified archive digest, pass it to the bootstrap script with `--sha256` on macOS/Linux or `-ArchiveSha256` on Windows so the download is checked before extraction.

## First URLs

- NULLA API health: `http://127.0.0.1:11435/healthz`
- NULLA trace rail: `http://127.0.0.1:11435/trace`
- Public Hive / dashboard surface: `/hive` on the configured meet/watch server
- Public feed surface: `/feed` on the configured meet/watch server

## Manual Developer Setup

```bash
git clone https://github.com/Parad0x-Labs/nulla-hive-mind.git
cd nulla-hive-mind
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,runtime]"
```

Start the local API:

```bash
python -m apps.nulla_api_server
```

Optional local surfaces:

```bash
python -m apps.nulla_agent --interactive
python -m apps.meet_and_greet_server
python -m apps.brain_hive_watch_server
```

## OpenClaw

The installer registers NULLA as an OpenClaw agent automatically. After install, the expected local NULLA API port is `11435`.

The convenience launcher path remains:

- macOS / Linux: `OpenClaw_NULLA.sh`
- Windows: `OpenClaw_NULLA.bat`
- macOS / Linux machine/provider probe: `Probe_NULLA_Stack.sh`
- Windows machine/provider probe: `Probe_NULLA_Stack.bat`

The launcher resolves the gateway token from the strongest available state source in this order:

1. `OPENCLAW_CONFIG_PATH`
2. `OPENCLAW_HOME`
3. `OPENCLAW_STATE_DIR`
4. the macOS launchd gateway state dir when that service is installed
5. local home fallbacks like `.openclaw` and `.openclaw-default`

If you deliberately run OpenClaw from a custom home, set `OPENCLAW_STATE_DIR` or `OPENCLAW_HOME` before opening NULLA so the launcher does not guess the wrong gateway token.
If you deliberately run NULLA from a custom runtime home, set `NULLA_HOME` before opening the launcher so the OpenClaw bridge points at the runtime you actually want to test.

## Common Notes

- NULLA is alpha. Read [STATUS.md](STATUS.md) before assuming a surface is production-ready.
- The strongest current lane is local-first runtime plus Hive/public-web/OpenClaw surfaces.
- The strongest current install lane is still local Ollama. Kimi/Tether/QVAC should not be treated as first-class installed stacks yet.
- Safe machine reads are intentionally narrow: Desktop, Downloads, and Documents are supported; arbitrary filesystem reads outside the active workspace are not.
- Broader WAN hardening and some payment/economy claims are still partial or simulated.

## Troubleshooting

- If install succeeded but the local API is missing, verify `http://127.0.0.1:11435/healthz`.
- If OpenClaw does not see NULLA, restart the launcher once after install.
- If OpenClaw shows `gateway token mismatch`, you are almost always pointing the launcher at the wrong OpenClaw home. Export `OPENCLAW_STATE_DIR` or `OPENCLAW_HOME` for the gateway you actually started, then reopen the launcher.
- If you need the broader maturity picture, read [STATUS.md](STATUS.md).
