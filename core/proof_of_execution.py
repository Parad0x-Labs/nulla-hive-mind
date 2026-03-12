from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ProofReceipt:
    receipt_id: str
    task_id: str
    helper_peer_id: str
    result_hash: str
    started_at: str
    finished_at: str
    proof_hash: str


def create_proof_receipt(*, receipt_id: str, task_id: str, helper_peer_id: str, result_hash: str, started_at: str, finished_at: str) -> ProofReceipt:
    payload = {
        "receipt_id": receipt_id,
        "task_id": task_id,
        "helper_peer_id": helper_peer_id,
        "result_hash": result_hash,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    proof_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return ProofReceipt(proof_hash=proof_hash, **payload)


def verify_proof_receipt(receipt: ProofReceipt) -> bool:
    expected = create_proof_receipt(
        receipt_id=receipt.receipt_id,
        task_id=receipt.task_id,
        helper_peer_id=receipt.helper_peer_id,
        result_hash=receipt.result_hash,
        started_at=receipt.started_at,
        finished_at=receipt.finished_at,
    )
    return expected.proof_hash == receipt.proof_hash
