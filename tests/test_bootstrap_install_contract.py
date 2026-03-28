from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_shell_bootstrap_falls_back_to_canonical_installer() -> None:
    script = (PROJECT_ROOT / "installer" / "bootstrap_nulla.sh").read_text(encoding="utf-8")

    assert "--install-profile <id>" in script
    assert "ollama-only" in script
    assert "ollama+kimi" in script
    assert "ollama+tether" in script
    assert "NULLA_INSTALL_PROFILE" in script
    assert 'BUILD_COMMIT=""' in script
    assert 'resolve_archive_commit() {' in script
    assert 'write_build_metadata() {' in script
    assert 'config/build-source.json' in script
    assert 'profile_args=(--install-profile "${INSTALL_PROFILE}")' in script
    assert 'exec_with_profile_args() {' in script
    assert 'if [[ ${#profile_args[@]} -gt 0 ]]; then' in script
    assert '${INSTALL_DIR}/install_nulla.sh' in script
    assert '${INSTALL_DIR}/installer/install_nulla.sh' in script
    assert script.index('${INSTALL_DIR}/installer/install_nulla.sh') < script.index('${INSTALL_DIR}/install_nulla.sh')
    assert 'exec_with_profile_args "${launcher}"' in script
    assert 'exec_with_profile_args "${canonical}" --yes --start --openclaw default' in script
    assert 'exec_with_profile_args "${canonical}" --yes --openclaw default' in script
    assert 'no usable installer entrypoint was found' in script


def test_powershell_bootstrap_falls_back_to_canonical_installer() -> None:
    script = (PROJECT_ROOT / "installer" / "bootstrap_nulla.ps1").read_text(encoding="utf-8")

    assert '[string]$InstallProfile = $env:NULLA_INSTALL_PROFILE' in script
    assert 'function Resolve-ArchiveCommit' in script
    assert 'function Write-BuildMetadata' in script
    assert 'build-source.json' in script
    assert '/INSTALLPROFILE=$InstallProfile' in script
    assert 'install_nulla.bat' in script
    assert 'installer\\\\install_nulla.bat' in script
    assert script.index('installer\\\\install_nulla.bat') < script.index('install_nulla.bat')
    assert '& $canonical /Y /START "/OPENCLAW=default" @profileArgs' in script
    assert '& $canonical /Y "/OPENCLAW=default" @profileArgs' in script
    assert 'no usable installer entrypoint was found' in script


def test_shell_bootstrap_executes_launcher_without_profile_override(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    archive_root = source_root / "nulla-hive-mind-main"
    install_dir = tmp_path / "install"
    marker_path = tmp_path / "launcher_args.txt"
    archive_path = tmp_path / "nulla-bootstrap.tar.gz"

    archive_root.mkdir(parents=True)
    (archive_root / "Install_And_Run_NULLA.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'printf "%s\\n" "$@" > "{marker_path}"\n',
        encoding="utf-8",
    )
    (archive_root / "Install_And_Run_NULLA.sh").chmod(0o755)

    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(archive_root, arcname=archive_root.name)

    subprocess.run(
        [
            "bash",
            str(PROJECT_ROOT / "installer" / "bootstrap_nulla.sh"),
            "--archive-url",
            archive_path.resolve().as_uri(),
            "--dir",
            str(install_dir),
        ],
        check=True,
        cwd=PROJECT_ROOT,
    )

    assert marker_path.exists()
    assert marker_path.read_text(encoding="utf-8") == "\n"
    metadata_path = install_dir / "config" / "build-source.json"
    assert metadata_path.exists()
    metadata = metadata_path.read_text(encoding="utf-8")
    assert '"ref": "main"' in metadata
    assert f'"source_url": "{archive_path.resolve().as_uri()}"' in metadata
