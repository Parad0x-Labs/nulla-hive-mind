from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:18080"
DEFAULT_LEVELS = (1, 2, 4)


@dataclass(frozen=True)
class RequestMeasurement:
    concurrency: int
    request_index: int
    success: bool
    latency_seconds: float
    status_code: int
    response_bytes: int
    token: str
    error: str | None = None


def _parse_levels(raw: str) -> tuple[int, ...]:
    values = []
    for piece in str(raw or "").split(","):
        clean = piece.strip()
        if not clean:
            continue
        level = int(clean)
        if level <= 0:
            raise ValueError("concurrency levels must be positive integers")
        values.append(level)
    if not values:
        raise ValueError("at least one concurrency level is required")
    return tuple(values)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return round(float(values[0]), 3)
    rank = (len(values) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(float(values[lower]), 3)
    weight = rank - lower
    interpolated = values[lower] * (1.0 - weight) + values[upper] * weight
    return round(float(interpolated), 3)


def summarize_measurements(
    rows: list[RequestMeasurement],
    *,
    wall_seconds: float,
) -> dict[str, float | int]:
    latencies = sorted(float(row.latency_seconds) for row in rows)
    successes = sum(1 for row in rows if row.success)
    total = len(rows)
    success_rate = round(successes / total, 4) if total else 0.0
    throughput = round(successes / wall_seconds, 3) if wall_seconds > 0 else 0.0
    return {
        "total_requests": total,
        "successes": successes,
        "success_rate": success_rate,
        "throughput_rps": throughput,
        "wall_seconds": round(wall_seconds, 3),
        "p50_seconds": _percentile(latencies, 0.50),
        "p95_seconds": _percentile(latencies, 0.95),
        "p99_seconds": _percentile(latencies, 0.99),
        "max_seconds": round(max(latencies), 3) if latencies else 0.0,
        "mean_seconds": round(statistics.fmean(latencies), 3) if latencies else 0.0,
    }


async def _run_one(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    workspace_root: Path,
    concurrency: int,
    request_index: int,
) -> RequestMeasurement:
    token = f"GREENLOOP-{concurrency}-{request_index}"
    workspace = workspace_root / f"c{concurrency}" / f"req-{request_index:02d}"
    workspace.mkdir(parents=True, exist_ok=True)
    body = {
        "model": "nulla",
        "messages": [{"role": "user", "content": f"Reply with exactly {token} and nothing else."}],
        "stream": False,
        "workspace": str(workspace),
        "conversationId": f"greenloop-concurrency-{concurrency}-{request_index}",
    }
    started = time.perf_counter()
    try:
        response = await client.post(f"{base_url.rstrip('/')}/api/chat", json=body)
        latency = round(time.perf_counter() - started, 3)
        payload = response.json()
        content = str(payload.get("message", {}).get("content") or "")
        success = response.status_code == 200 and token in content
        return RequestMeasurement(
            concurrency=concurrency,
            request_index=request_index,
            success=success,
            latency_seconds=latency,
            status_code=int(response.status_code),
            response_bytes=len(response.content),
            token=token,
            error=None if success else f"unexpected response content: {content[:160]}",
        )
    except Exception as exc:  # pragma: no cover - defensive runtime capture
        latency = round(time.perf_counter() - started, 3)
        return RequestMeasurement(
            concurrency=concurrency,
            request_index=request_index,
            success=False,
            latency_seconds=latency,
            status_code=0,
            response_bytes=0,
            token=token,
            error=str(exc),
        )


async def _measure_level(
    *,
    base_url: str,
    workspace_root: Path,
    concurrency: int,
    requests_per_level: int,
    timeout_seconds: float,
) -> tuple[list[RequestMeasurement], float]:
    total_requests = max(concurrency, concurrency * requests_per_level)
    started = time.perf_counter()
    rows: list[RequestMeasurement] = []
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        next_index = 0
        while next_index < total_requests:
            batch_size = min(concurrency, total_requests - next_index)
            tasks = [
                _run_one(
                    client,
                    base_url=base_url,
                    workspace_root=workspace_root,
                    concurrency=concurrency,
                    request_index=next_index + offset,
                )
                for offset in range(batch_size)
            ]
            rows.extend(await asyncio.gather(*tasks))
            next_index += batch_size
    wall_seconds = round(time.perf_counter() - started, 3)
    return rows, wall_seconds


def _write_summary_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "concurrency",
        "total_requests",
        "successes",
        "success_rate",
        "throughput_rps",
        "wall_seconds",
        "p50_seconds",
        "p95_seconds",
        "p99_seconds",
        "max_seconds",
        "mean_seconds",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the NULLA greenloop concurrency probe.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--levels", default="1,2,4")
    parser.add_argument("--requests-per-level", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--workspace-root", default="artifacts/greenloop_concurrency/workspace")
    parser.add_argument("--output-csv", default="reports/greenloop/concurrency.csv")
    parser.add_argument("--output-json", default="reports/greenloop/concurrency.json")
    return parser


async def _async_main(args: argparse.Namespace) -> int:
    levels = _parse_levels(args.levels)
    workspace_root = Path(args.workspace_root).resolve()
    summary_rows: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []
    for concurrency in levels:
        measurements, wall_seconds = await _measure_level(
            base_url=args.base_url,
            workspace_root=workspace_root,
            concurrency=concurrency,
            requests_per_level=max(1, int(args.requests_per_level)),
            timeout_seconds=float(args.timeout_seconds),
        )
        summary = summarize_measurements(measurements, wall_seconds=wall_seconds)
        summary_rows.append({"concurrency": concurrency, **summary})
        raw_rows.extend(asdict(row) for row in measurements)
    _write_summary_csv(Path(args.output_csv), summary_rows)
    _write_json(
        Path(args.output_json),
        {
            "base_url": args.base_url,
            "levels": list(levels),
            "requests_per_level": int(args.requests_per_level),
            "workspace_root": str(workspace_root),
            "summary": summary_rows,
            "measurements": raw_rows,
        },
    )
    return 0 if all(float(row["success_rate"]) == 1.0 for row in summary_rows) else 1


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
