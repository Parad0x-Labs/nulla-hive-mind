from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_shell_bootstrap_falls_back_to_canonical_installer() -> None:
    script = (PROJECT_ROOT / "installer" / "bootstrap_nulla.sh").read_text(encoding="utf-8")

    assert '${INSTALL_DIR}/install_nulla.sh' in script
    assert '${INSTALL_DIR}/installer/install_nulla.sh' in script
    assert script.index('${INSTALL_DIR}/installer/install_nulla.sh') < script.index('${INSTALL_DIR}/install_nulla.sh')
    assert 'exec "${canonical}" --yes --start --openclaw default' in script
    assert 'exec "${canonical}" --yes --openclaw default' in script
    assert 'no usable installer entrypoint was found' in script


def test_powershell_bootstrap_falls_back_to_canonical_installer() -> None:
    script = (PROJECT_ROOT / "installer" / "bootstrap_nulla.ps1").read_text(encoding="utf-8")

    assert 'install_nulla.bat' in script
    assert 'installer\\\\install_nulla.bat' in script
    assert script.index('installer\\\\install_nulla.bat') < script.index('install_nulla.bat')
    assert '& $canonical /Y /START "/OPENCLAW=default"' in script
    assert '& $canonical /Y "/OPENCLAW=default"' in script
    assert 'no usable installer entrypoint was found' in script
