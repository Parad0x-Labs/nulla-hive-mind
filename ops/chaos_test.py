from __future__ import annotations

import random

from network.chunk_protocol import chunk_payload, reassemble_chunks


def simulate_packet_loss(payload: bytes, *, drop_rate: float = 0.25) -> dict:
    manifest, frames = chunk_payload("chaos-transfer", payload, chunk_size=32)
    kept = [frame for frame in frames if random.random() > drop_rate]
    success = False
    try:
        reassemble_chunks(manifest, kept)
        success = len(kept) == len(frames)
    except Exception:
        success = False
    return {"frames_total": len(frames), "frames_kept": len(kept), "reassembly_success": success}


def main() -> int:
    result = simulate_packet_loss(b"nulla-chaos-test-payload" * 40)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
