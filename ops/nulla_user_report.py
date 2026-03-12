from __future__ import annotations

import argparse
import json

from core.nulla_user_summary import build_user_summary, render_user_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nulla-user-report")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    parser.add_argument("--limit", type=int, default=5, help="Number of recent items to show per section.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = build_user_summary(limit_recent=max(1, min(int(args.limit), 20)))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_user_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
