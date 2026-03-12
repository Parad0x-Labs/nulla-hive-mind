from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.public_hive_bridge import ensure_public_hive_auth


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ensure_public_hive_auth",
        description="Hydrate runtime public Hive auth from bundled config or the watch node when available.",
    )
    parser.add_argument("--project-root", default=str(PROJECT_ROOT), help="Project root used for bundled config and key discovery.")
    parser.add_argument("--target-path", default="", help="Optional explicit agent-bootstrap.json target path.")
    parser.add_argument("--watch-host", default="161.35.145.74", help="Watch node host or IP.")
    parser.add_argument("--watch-user", default="root", help="Watch node SSH user.")
    parser.add_argument(
        "--remote-config-path",
        default="/opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/watch-edge-1.json",
        help="Remote watch-node JSON config path that contains the live auth token.",
    )
    parser.add_argument("--require-auth", action="store_true", help="Exit non-zero when public Hive is configured but write auth is still missing.")
    parser.add_argument("--json", action="store_true", help="Print the result as JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    target_path = Path(args.target_path).expanduser() if str(args.target_path or "").strip() else None
    result = ensure_public_hive_auth(
        project_root=Path(args.project_root).expanduser(),
        target_path=target_path,
        watch_host=str(args.watch_host or "").strip() or "161.35.145.74",
        watch_user=str(args.watch_user or "").strip() or "root",
        remote_config_path=str(args.remote_config_path or "").strip()
        or "/opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/watch-edge-1.json",
        require_auth=bool(args.require_auth),
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
    else:
        print(f"{result.get('status')}: {result.get('target_path') or 'no target'}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
