from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


_TEST_RUNTIME_HOME: Path | None = None


def pytest_configure(config) -> None:
    del config
    global _TEST_RUNTIME_HOME
    if _TEST_RUNTIME_HOME is None:
        _TEST_RUNTIME_HOME = Path(tempfile.mkdtemp(prefix="nulla_pytest_home_", dir="/tmp")).resolve()
    os.environ["NULLA_HOME"] = str(_TEST_RUNTIME_HOME)


def pytest_unconfigure(config) -> None:
    del config
    global _TEST_RUNTIME_HOME
    if _TEST_RUNTIME_HOME is None:
        return
    shutil.rmtree(_TEST_RUNTIME_HOME, ignore_errors=True)
    _TEST_RUNTIME_HOME = None
