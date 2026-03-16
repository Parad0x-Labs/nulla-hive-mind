# NULLA Non-Technical Install Walkthrough

This walkthrough is for users who just want:

- extract,
- click installer,
- answer simple prompts,
- run NULLA.

## What To Send Users

Send one installer archive from:

- `build/installer/*.zip` (recommended for all users),
- `build/installer/*.tar.gz` (Linux/macOS),
- `build/installer/*.rar` (only if `rar` was available during build).

## Build The Installer Archive

From project root:

```bash
bash ops/build_installer_bundle.sh
```

For internal closed-test Hive bundles that must create/claim/post immediately on auth-required clusters:

```bash
bash ops/build_installer_bundle.sh --embed-public-hive-auth
```

That flag is sensitive and should stay internal-only. Without it, the bundle can still run NULLA, but public Hive writes depend on runtime auth hydration on the target machine.

## Fastest One-Line Install + Launch

Linux/macOS:

```bash
bash Install_And_Run_NULLA.sh
```

Windows:

- double-click `Install_And_Run_NULLA.bat`

This path runs non-interactive defaults, creates OpenClaw bridge launcher, and starts NULLA immediately.
It now also:
- checks/install Ollama,
- probes hardware and chooses the best supported Qwen tier,
- seeds the visible agent name into runtime storage and OpenClaw,
- attempts OpenClaw config through `ollama launch openclaw --config`,
- installs Playwright + Chromium for browser-backed web fallback,
- attempts to boot local SearXNG on `http://127.0.0.1:8080` if Docker is available,
- patches NULLA into `~/.openclaw/openclaw.json`,
- writes `install_receipt.json` for support/debugging.
To directly chat in terminal after install, run `Talk_To_NULLA.(sh|command|bat)`.
Installer also creates a Desktop shortcut for one-click OpenClaw + NULLA launch.

Future public GitHub one-command path:

Linux/macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/Parad0x-Labs/nulla-hive-mind/main/installer/bootstrap_nulla.sh | bash
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/Parad0x-Labs/nulla-hive-mind/main/installer/bootstrap_nulla.ps1 | iex"
```

## Local "Baby NULLA" Worker Pool (Single Machine)

NULLA now supports local split execution lanes on one machine.

- Auto mode: picks recommended helper capacity from CPU + available RAM (and CUDA VRAM when present).
- Hard default cap: `10` local helper lanes.
- Manual override: allowed, with startup warning if you exceed recommended capacity.

Quick override examples:

Linux/macOS:

```bash
NULLA_DAEMON_CAPACITY=10 bash Start_NULLA.sh
```

Windows (CMD):

```bat
set NULLA_DAEMON_CAPACITY=10
Start_NULLA.bat
```

If the machine is too small for your override, NULLA still starts but prints a clear stability warning.

## End-User Flow (Linux/macOS)

1. Create a folder like `NULLA`.
2. Extract the installer archive into that folder.
3. Open extracted `nulla-hive-mind_Installer`.
4. Run:
   - macOS double-click: `Install_NULLA.command`
   - terminal: `bash Install_NULLA.sh`
5. Click Enter / accept defaults:
   - runtime folder,
   - agent display name,
   - optional OpenClaw integration.
6. Wait for:
   - dependency install,
   - Playwright browser runtime install,
   - local SearXNG bootstrap attempt,
   - hardware probe + model selection,
   - Ollama check/install,
   - OpenClaw configuration and NULLA registration.
7. Start or reopen with:
   - `OpenClaw_NULLA.command` (macOS),
   - `bash OpenClaw_NULLA.sh` (Linux/macOS).
8. Talk to NULLA:
   - `Talk_To_NULLA.command` (macOS),
   - `bash Talk_To_NULLA.sh` (Linux/macOS).

## End-User Flow (Windows)

1. Create a folder like `NULLA`.
2. Extract the installer archive.
3. Open extracted `nulla-hive-mind_Installer`.
4. Double-click `Install_NULLA.bat`.
5. Accept defaults:
   - runtime folder,
   - agent display name,
   - optional OpenClaw integration.
6. Wait for:
   - dependency install,
   - Playwright browser runtime install,
   - local SearXNG bootstrap attempt,
   - hardware probe + model selection,
   - Ollama check/install,
   - OpenClaw configuration and NULLA registration.
7. Start NULLA by double-clicking `OpenClaw_NULLA.bat`.
8. Talk to NULLA by double-clicking `Talk_To_NULLA.bat`.

## OpenClaw Bridge

Installer can generate a launcher in:

- Linux/macOS default: `~/.openclaw/agents/main/agent/nulla`
- Windows default: `%USERPROFILE%\.openclaw\agents\main\agent\nulla`

This bridge is now only the compatibility layer.
The installer also patches the main OpenClaw config at `~/.openclaw/openclaw.json` (or the Windows equivalent) so NULLA appears in the agent list even when bridge-folder discovery is not enough by itself.

The install writes `install_receipt.json` in the extracted folder with:
- selected model,
- runtime path,
- OpenClaw config path,
- web stack defaults and expected local endpoints,
- launcher paths,
- Ollama binary path.

The install also writes `install_doctor.json`, which now reports whether public Hive writes are actually ready or still blocked by missing auth.

## Notes For Operators

- Installer creates a Python virtualenv in extracted folder: `.venv/`.
- Runtime data goes to user-selected `NULLA_HOME` (default user-home path).
- First install can take time because Python packages are downloaded.
- If `rar` tool is missing on build machine, zip/tar.gz are still valid outputs.
