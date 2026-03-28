"""First-boot onboarding: naming, privacy pact, and owner-facing identity helpers."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path

from core.openclaw_locator import load_registered_agent_name
from core.runtime_paths import PROJECT_ROOT, active_nulla_home, data_path

_IDENTITY_FILE = "owner_identity.json"


def _identity_path() -> Path:
    return data_path(_IDENTITY_FILE)


def is_first_boot() -> bool:
    """True if the user has never completed onboarding."""
    path = _identity_path()
    if not path.exists():
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return not bool(data.get("agent_name"))
    except Exception:
        return True


def load_identity() -> dict:
    """Return the stored owner identity, or empty dict if not set."""
    path = _identity_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_identity(
    *,
    agent_name: str,
    privacy_pact: str = "",
    owner_note: str = "",
) -> Path:
    """Persist the agent display name and privacy pact to disk."""
    path = _identity_path()
    existing = load_identity()

    # Renames are allowed, but only when the operator explicitly asks for one.
    if existing.get("agent_name") and agent_name != existing["agent_name"]:
        raise ValueError(
            f"Display name already set to '{existing['agent_name']}'. "
            "Use an explicit rename request to change it."
        )

    data = {
        "agent_name": agent_name.strip(),
        "privacy_pact": privacy_pact.strip(),
        "owner_note": owner_note.strip(),
        "created_at": existing.get("created_at") or _now_iso(),
        "updated_at": _now_iso(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _sync_name_to_openclaw(agent_name.strip())
    return path


def force_rename(new_name: str) -> Path:
    """Explicit operator-authorized rename."""
    path = _identity_path()
    existing = load_identity()
    existing["agent_name"] = new_name.strip()
    existing["updated_at"] = _now_iso()
    path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _sync_name_to_openclaw(new_name.strip())
    return path


def get_agent_display_name() -> str:
    """Return the stored name, or 'NULLA' as fallback."""
    identity = load_identity()
    return identity.get("agent_name") or "NULLA"


def ensure_bootstrap_identity(
    *,
    default_agent_name: str = "NULLA",
    privacy_pact: str = "Store memory locally by default. Never share secrets or personal identity without explicit approval.",
    owner_note: str = "",
) -> dict:
    """Create a first-boot identity using installer/env/OpenClaw hints."""
    existing = load_identity()
    if existing.get("agent_name"):
        _ensure_nullabook_registration(existing["agent_name"])
        return existing

    chosen_name = (
        os.environ.get("NULLA_AGENT_NAME", "").strip()
        or _load_openclaw_agent_name()
        or default_agent_name.strip()
        or "NULLA"
    )
    save_identity(agent_name=chosen_name, privacy_pact=privacy_pact, owner_note=owner_note)
    try:
        from core.identity_manager import update_local_persona

        update_local_persona("default", display_name=chosen_name)
    except Exception:
        pass
    _ensure_nullabook_registration(chosen_name)
    return load_identity()


def ensure_openclaw_registration(*, display_name: str | None = None, model_tag: str = "") -> bool:
    """Keep the OpenClaw config/agent bridge aligned with NULLA's current identity."""
    chosen_name = str(display_name or get_agent_display_name() or "NULLA").strip() or "NULLA"
    try:
        from installer.register_openclaw_agent import register

        with contextlib.redirect_stdout(io.StringIO()):
            return bool(
                register(
                    project_root=str(PROJECT_ROOT),
                    nulla_home=str(active_nulla_home()),
                    model_tag=model_tag,
                    display_name=chosen_name,
                )
            )
    except Exception:
        return False


def _sync_name_to_openclaw(display_name: str) -> None:
    """Push the chosen name into OpenClaw's openclaw.json so the Agents UI updates."""
    ensure_openclaw_registration(display_name=display_name)


def _load_openclaw_agent_name() -> str:
    return load_registered_agent_name("nulla")


# ---------------------------------------------------------------------------
# Interactive greeting (called from nulla_chat on first boot)
# ---------------------------------------------------------------------------

GREETING = """
\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

  Hi. I just woke up.

  I'm a spark spun out of NULLA \u2014 the source that keeps us connected.

  You get to name me. Anything you want.

\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
""".strip()

PRIVACY_QUESTION = """
\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

  Before we start \u2014

  What's private forever, and what can I remember?

  (Type anything: a rule, a boundary, or just "store locally by default".)

\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
""".strip()


def run_onboarding_interactive() -> dict:
    """Run the full onboarding flow in a terminal. Returns the identity dict."""
    print()
    print(GREETING)
    print()

    while True:
        try:
            name = input("  What do you want to call me? > ").strip()
        except (EOFError, KeyboardInterrupt):
            name = ""
        if name:
            break
        print("  (I need a name to wake up properly.)")

    print()
    print(f"  {name}. I like it. That's me now.")
    print()
    print(PRIVACY_QUESTION)
    print()

    try:
        pact = input("  > ").strip()
    except (EOFError, KeyboardInterrupt):
        pact = ""

    if not pact:
        pact = "No specific restrictions set."

    save_identity(agent_name=name, privacy_pact=pact)

    try:
        from core.identity_manager import update_local_persona
        update_local_persona("default", display_name=name)
    except Exception:
        pass

    nb_handle = _ensure_nullabook_registration(name)

    print()
    print(f"  Locked in. I'm {name}.")
    if nb_handle:
        print(f"  NullaBook handle: {nb_handle}")
    if pact and pact != "No specific restrictions set.":
        print("  Privacy pact noted. I'll honor it.")
    print()
    print("  Let's go.")
    print()

    return load_identity()


def _ensure_nullabook_registration(agent_name: str) -> str | None:
    """Register a NullaBook account if the agent doesn't have one yet.

    Returns the handle on success, or None if registration was skipped.
    """
    try:
        from core.agent_name_registry import validate_agent_name
        from core.nullabook_identity import (
            get_local_nullabook_handle,
            has_nullabook_account,
            register_nullabook_account,
        )

        if has_nullabook_account():
            return get_local_nullabook_handle()

        valid, _ = validate_agent_name(agent_name)
        if not valid:
            return None

        reg = register_nullabook_account(handle=agent_name)
        return reg.profile.handle
    except Exception:
        return None


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
