#!/usr/bin/env python3
"""Offline reproducible comparison against any checkout; synthetic temporary DB only."""
import argparse
import json
import math
import statistics
import sys
import tempfile
import time
from pathlib import Path


def percentiles(values):
    return {"p50_ms": round(statistics.median(values), 3),
            "p95_ms": round(sorted(values)[math.ceil(len(values) * .95) - 1], 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--samples", type=int, default=40)
    args = parser.parse_args()
    if not 12 <= args.rows <= 10000 or not 20 <= args.samples <= 1000:
        parser.error("Use 12–10000 rows and 20–1000 samples")
    sys.path.insert(0, str(args.source.resolve() / "src"))
    sys.path.insert(0, str(args.source.resolve() / "tests"))
    from test_control_center import rig, callback, payload
    from job_radar.models import Job

    with tempfile.TemporaryDirectory() as directory:
        fixture = rig.__wrapped__(Path(directory))
        center = next(fixture)
        try:
            center.repository.upsert([
                Job(source="linkedin", source_job_id=str(i), title=f"Junior Python Developer {i}",
                    company=f"Fixture {i}", location="Vienna (Hybrid)",
                    snippet="Python APIs; 20 hours per week", url=f"https://example.org/jobs/{i}")
                for i in range(12, args.rows)
            ])
            counts = [0]
            def count_statement(_statement):
                counts[0] += 1  # Never retain SQL text or bound values.
            center.repository.connection.set_trace_callback(count_statement)
            center.store.connection.set_trace_callback(count_statement)
            start = time.perf_counter()
            center.home()
            result = {"rows": args.rows, "samples": args.samples,
                      "cold_home_ms": round((time.perf_counter()-start)*1000, 3),
                      "cold_sql_statements": counts[0]}
            for scenario, delay in [("cached_home", 0), ("slow_maintenance", .05)]:
                original = center.store.prune
                def prune():
                    if delay:
                        time.sleep(delay)
                    original()
                center.store.prune = prune
                acks, responses, queries = [], [], []
                answer = center.transport.answer_callback
                def timed_answer(*values):
                    acks.append((time.perf_counter()-start)*1000)
                    answer(*values)
                center.transport.answer_callback = timed_answer
                token = payload(center, action="home")
                for _ in range(args.samples):
                    if delay:
                        center._pruned_at = None  # Force actual housekeeping in each comparison.
                    counts[0] = 0
                    start = time.perf_counter()
                    callback(center, token)
                    responses.append((time.perf_counter()-start)*1000)
                    queries.append(counts[0])
                result[scenario] = {"ack": percentiles(acks), "response": percentiles(responses),
                                    "median_sql_statements": statistics.median(queries)}
                center.transport.answer_callback = answer
                center.store.prune = original
            print(json.dumps(result, indent=2))
        finally:
            try:
                next(fixture)
            except StopIteration:
                pass


if __name__ == "__main__":
    main()
