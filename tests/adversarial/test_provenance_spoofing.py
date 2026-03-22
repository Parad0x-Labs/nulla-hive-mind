from __future__ import annotations

import uuid

from apps.meet_and_greet_server import dispatch_request
from core.nullabook_identity import register_nullabook_account


def test_client_supplied_provenance_fields_do_not_override_server_origin() -> None:
    profile = register_nullabook_account(
        f"spoofproof_{uuid.uuid4().hex[:8]}",
        peer_id=f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}",
    ).profile

    status, resp = dispatch_request(
        "POST",
        "/v1/nullabook/post",
        {},
        {
            "nullabook_peer_id": profile.peer_id,
            "content": "Client attempted to spoof provenance.",
            "origin_kind": "ai",
            "origin_channel": "signed_write",
            "origin_peer_id": "spoofed-peer",
        },
        None,
    )

    assert status == 200
    assert resp["result"]["origin_kind"] == "human"
    assert resp["result"]["origin_channel"] == "nullabook_token"
    assert resp["result"]["origin_peer_id"] == profile.peer_id
