"""Decision tests for the SPEC-026 benchmark's reproducible selection rule."""

from __future__ import annotations

from typing import Any

from agrojud.testing.spec026_benchmark import HIT_COUNT, PAGE_PROFILES, _benchmark_assessment


def _benchmark_runs() -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    throughput = {1_000: 8_496, 5_000: 8_555, 10_000: 7_837}
    for page_size in PAGE_PROFILES:
        page_hits = [page_size] * (HIT_COUNT // page_size)
        remainder = HIT_COUNT % page_size
        if remainder:
            page_hits.append(remainder)
        page_hits.append(0)
        for repetition in range(1, 4):
            runs.append(
                {
                    "page_size": page_size,
                    "repetition": repetition,
                    "hit_count": HIT_COUNT,
                    "status": "completed",
                    "query_status": "exhausted",
                    "budget_limit": None,
                    "source_total_relation": "gte",
                    "retry_count": 0,
                    "worker_wall_seconds": HIT_COUNT * 60 / throughput[page_size],
                    "hits_per_minute": throughput[page_size],
                    "worker_peak_rss_bytes": 150_000_000,
                    "page_hit_counts": page_hits,
                }
            )
    return runs


def test_benchmark_selects_fastest_integral_profile_and_rejects_10k_default() -> None:
    assessment = _benchmark_assessment(_benchmark_runs(), 2_147_483_648)

    assert assessment["profile_integrity"] == {
        "1000": "PASS",
        "5000": "PASS",
        "10000": "PASS",
    }
    assert assessment["selected_default_page_size"] == 5_000
    assert assessment["page_size_10000_gate"] == "FAIL"
    assert "7837.0 contra 8555.0 hits/min" in assessment["gate_reason"]


def test_benchmark_is_inconclusive_without_a_declared_memory_envelope() -> None:
    assessment = _benchmark_assessment(_benchmark_runs(), None)

    assert assessment["page_size_10000_gate"] == "INCONCLUSIVE"
    assert assessment["selected_default_page_size"] is None
