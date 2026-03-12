from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.brain_hive_watch_server import serve
from core.brain_hive_watch_config_loader import load_brain_hive_watch_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_brain_hive_watch_from_config",
        description="Start the Brain Hive watch-edge server from a JSON config file.",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to the watch-edge JSON config file.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config = load_brain_hive_watch_config(Path(args.config))
    print(
        f"[watch-edge] running bind={config.host}:{config.port} "
        f"upstreams={len(config.upstream_base_urls)}"
    )
    serve(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
