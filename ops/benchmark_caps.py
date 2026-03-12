from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class BenchmarkResult:
    compute_class: str
    hashes_per_second: float
    benchmark_ms: int
    max_safe_concurrency: int


def measure_local_capabilities(iterations: int = 50_000) -> BenchmarkResult:
    start = time.perf_counter()
    seed = b"nulla-benchmark"
    for idx in range(iterations):
        hashlib.sha256(seed + idx.to_bytes(4, "big")).digest()
    elapsed = max(0.000001, time.perf_counter() - start)
    hps = iterations / elapsed
    if hps >= 220_000:
        compute_class = "gpu_elite"
        concurrency = 8
    elif hps >= 120_000:
        compute_class = "gpu_basic"
        concurrency = 4
    elif hps >= 45_000:
        compute_class = "cpu_advanced"
        concurrency = 2
    else:
        compute_class = "cpu_basic"
        concurrency = 1
    return BenchmarkResult(
        compute_class=compute_class,
        hashes_per_second=round(hps, 2),
        benchmark_ms=int(elapsed * 1000),
        max_safe_concurrency=concurrency,
    )


def main() -> int:
    result = measure_local_capabilities()
    print(json.dumps(result.__dict__, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
