from __future__ import annotations

import argparse
import time
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.meet_and_greet_node import MeetAndGreetNode
from core.meet_and_greet_config_loader import load_meet_node_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_meet_node_from_config",
        description="Start one meet-and-greet node from a JSON config file.",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to one of the cluster node JSON files.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    config = load_meet_node_config(Path(args.config))
    node = MeetAndGreetNode(config)
    node.start()
    print(
        f"[meet-node] running node_id={config.node_id} region={config.region} "
        f"bind={config.bind_host}:{config.bind_port} public={config.public_base_url}"
    )
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("[meet-node] stopping")
        node.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
