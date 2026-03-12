"""Seed the first-run NULLA display identity into runtime storage."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.identity_manager import update_local_persona
from core.onboarding import ensure_bootstrap_identity, force_rename, load_identity
from core.credit_ledger import ensure_starter_credits
from network.signer import get_local_peer_id


def ensure_identity(*, agent_name: str, privacy_pact: str, owner_note: str, force: bool) -> str:
    current = load_identity()
    requested = str(agent_name or "").strip() or "NULLA"
    if current.get("agent_name"):
        chosen = str(current.get("agent_name") or requested).strip() or requested
        if force and chosen != requested:
            force_rename(requested)
            chosen = requested
        try:
            update_local_persona("default", display_name=chosen)
        except Exception:
            pass
        return chosen

    seeded = ensure_bootstrap_identity(
        default_agent_name=requested,
        privacy_pact=privacy_pact,
        owner_note=owner_note,
    )
    chosen = str(seeded.get("agent_name") or requested).strip() or requested
    try:
        update_local_persona("default", display_name=chosen)
    except Exception:
        pass
    try:
        ensure_starter_credits(get_local_peer_id())
    except Exception:
        pass
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser(prog="seed_identity")
    parser.add_argument("--agent-name", default="NULLA")
    parser.add_argument("--privacy-pact", default="remember everything")
    parser.add_argument("--owner-note", default="")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    print(
        ensure_identity(
            agent_name=str(args.agent_name),
            privacy_pact=str(args.privacy_pact),
            owner_note=str(args.owner_note),
            force=bool(args.force),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
