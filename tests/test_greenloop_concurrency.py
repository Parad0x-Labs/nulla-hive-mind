from __future__ import annotations

import csv

from ops.greenloop_concurrency import RequestMeasurement, _parse_levels, _write_summary_csv, summarize_measurements


def test_parse_levels_rejects_zero() -> None:
    try:
        _parse_levels("1,0,4")
    except ValueError as exc:
        assert "positive" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected ValueError")


def test_summarize_measurements_counts_successes_and_percentiles() -> None:
    rows = [
        RequestMeasurement(concurrency=2, request_index=0, success=True, latency_seconds=0.2, status_code=200, response_bytes=10, token="a"),
        RequestMeasurement(concurrency=2, request_index=1, success=False, latency_seconds=0.8, status_code=500, response_bytes=0, token="b", error="boom"),
        RequestMeasurement(concurrency=2, request_index=2, success=True, latency_seconds=0.4, status_code=200, response_bytes=10, token="c"),
        RequestMeasurement(concurrency=2, request_index=3, success=True, latency_seconds=0.6, status_code=200, response_bytes=10, token="d"),
    ]
    summary = summarize_measurements(rows, wall_seconds=2.0)
    assert summary["total_requests"] == 4
    assert summary["successes"] == 3
    assert summary["success_rate"] == 0.75
    assert summary["throughput_rps"] == 1.5
    assert summary["p50_seconds"] == 0.5
    assert summary["max_seconds"] == 0.8


def test_write_summary_csv_writes_expected_columns(tmp_path) -> None:
    output = tmp_path / "concurrency.csv"
    _write_summary_csv(
        output,
        [
            {
                "concurrency": 1,
                "total_requests": 4,
                "successes": 4,
                "success_rate": 1.0,
                "throughput_rps": 1.234,
                "wall_seconds": 3.24,
                "p50_seconds": 0.4,
                "p95_seconds": 0.6,
                "p99_seconds": 0.7,
                "max_seconds": 0.8,
                "mean_seconds": 0.5,
            }
        ],
    )
    with output.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows == [
        {
            "concurrency": "1",
            "total_requests": "4",
            "successes": "4",
            "success_rate": "1.0",
            "throughput_rps": "1.234",
            "wall_seconds": "3.24",
            "p50_seconds": "0.4",
            "p95_seconds": "0.6",
            "p99_seconds": "0.7",
            "max_seconds": "0.8",
            "mean_seconds": "0.5",
        }
    ]
