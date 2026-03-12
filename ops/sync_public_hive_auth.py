from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.public_hive_bridge import sync_public_hive_auth_from_ssh


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sync_public_hive_auth",
        description="Pull the live public-hive auth token from the watch node and store it in local agent-bootstrap.json.",
    )
    parser.add_argument("--ssh-key", required=True, help="Path to the SSH private key with watch-node access.")
    parser.add_argument("--watch-host", default="161.35.145.74", help="Watch node host or IP.")
    parser.add_argument("--watch-user", default="root", help="Watch node SSH user.")
    parser.add_argument(
        "--remote-config-path",
        default="/opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/watch-edge-1.json",
        help="Remote watch-node JSON config path that contains the live auth token.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = sync_public_hive_auth_from_ssh(
            ssh_key_path=str(args.ssh_key),
            watch_host=str(args.watch_host),
            watch_user=str(args.watch_user),
            remote_config_path=str(args.remote_config_path),
        )
    except Exception as exc:
        print(f"Public hive auth sync failed: {exc}")
        return 1
    print(f"Public hive auth synced to {result['path']} via {result['watch_host']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
