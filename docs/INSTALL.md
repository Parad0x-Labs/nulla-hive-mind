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

## Common Notes

- NULLA is alpha. Read [STATUS.md](STATUS.md) before assuming a surface is production-ready.
- The strongest current lane is local-first runtime plus Hive/public-web/OpenClaw surfaces.
- Broader WAN hardening and some payment/economy claims are still partial or simulated.

## Troubleshooting

- If install succeeded but the local API is missing, verify `http://127.0.0.1:11435/healthz`.
- If OpenClaw does not see NULLA, restart the launcher once after install.
- If you need the broader maturity picture, read [STATUS.md](STATUS.md).
