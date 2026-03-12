from __future__ import annotations

import os


def _detect_available_memory_gb() -> float | None:
    try:
        import psutil  # type: ignore

        return float(psutil.virtual_memory().available) / (1024.0 ** 3)
    except Exception:
        return None


def _detect_available_vram_gb() -> float | None:
    try:
        import torch  # type: ignore

        if not bool(torch.cuda.is_available()):
            return None
        free_bytes, _total_bytes = torch.cuda.mem_get_info()
        return float(free_bytes) / (1024.0 ** 3)
    except Exception:
        return None


def recommend_local_worker_capacity(*, hard_cap: int = 10) -> int:
    cap = max(1, int(hard_cap))
    cpu_count = int(os.cpu_count() or 2)
    cpu_target = max(1, cpu_count // 2)

    mem_gb = _detect_available_memory_gb()
    if mem_gb is None:
        mem_target = cap
    else:
        # Conservative default: ~1.5 GB available RAM per helper lane.
        mem_target = max(1, int(mem_gb // 1.5))

    vram_gb = _detect_available_vram_gb()
    if vram_gb is None:
        vram_target = cap
    else:
        # Conservative default: ~2 GB free VRAM per helper lane when GPU is active.
        vram_target = max(1, int(vram_gb // 2.0))

    recommended = max(1, min(cap, cpu_target, mem_target, vram_target))
    return recommended


def resolve_local_worker_capacity(
    *,
    requested: int | None,
    hard_cap: int = 10,
) -> tuple[int, int]:
    cap = max(1, int(hard_cap))
    recommended = recommend_local_worker_capacity(hard_cap=cap)
    if requested is None:
        return recommended, recommended
    effective = max(1, min(int(requested), 32))
    return effective, recommended
