"""Benchmark fusejs (Python) against fuse.js (Node) on a shared workload.

Measures four things per engine, on the same corpus and the same queries:

* **startup**   — import/require plus index build, cold, in a fresh process
* **throughput** — searches per second, steady state
* **latency**   — per-search p50 / p95 / p99, from the full distribution
* **RSS**       — peak resident set size of the process

Python will lose on speed. That is expected and is not the point of the port
(see README); the number is reported honestly, distribution and confounders
included, rather than cherry-picked.

    python bench/run.py --out bench/results.json
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

WORDS = [
    "old",
    "man",
    "war",
    "lock",
    "artist",
    "code",
    "jeeves",
    "html",
    "angels",
    "demons",
    "silmarillion",
    "colony",
    "fool",
    "wooster",
    "the",
    "of",
    "and",
    "python",
    "javascript",
    "search",
    "fuzzy",
    "engine",
    "index",
    "score",
]


def build_corpus(size: int, seed: int = 42) -> list[dict[str, Any]]:
    """A deterministic synthetic corpus, shared by both engines."""
    rng = random.Random(seed)

    def phrase(count: int) -> str:
        return " ".join(rng.choice(WORDS) for _ in range(count))

    return [
        {
            "title": phrase(rng.randint(2, 6)),
            "author": {
                "firstName": phrase(1),
                "lastName": phrase(1),
            },
            "tags": [phrase(1) for _ in range(rng.randint(0, 3))],
        }
        for _ in range(size)
    ]


def build_queries(count: int, seed: int = 7) -> list[str]:
    rng = random.Random(seed)
    return [
        " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 3)))
        for _ in range(count)
    ]


def peak_rss_bytes() -> int | None:
    """Peak RSS of this process, or ``None`` where unavailable."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            kernel32 = ctypes.WinDLL("kernel32")
            psapi = ctypes.WinDLL("psapi")

            # Without an explicit restype ctypes truncates the pseudo-handle
            # to 32 bits and the call silently fails on 64-bit Windows.
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            psapi.GetProcessMemoryInfo.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(Counters),
                wintypes.DWORD,
            ]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            if psapi.GetProcessMemoryInfo(
                kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
            ):
                return int(counters.PeakWorkingSetSize)
        except (ImportError, OSError, AttributeError):
            return None
        return None

    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports kilobytes, macOS bytes.
        return int(usage) if sys.platform == "darwin" else int(usage) * 1024
    except (ImportError, OSError):
        return None


def percentile(values: list[float], fraction: float) -> float:
    """The value at ``fraction`` through the sorted distribution."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))
    return ordered[index]


def measure_python(
    corpus: list[dict[str, Any]], queries: list[str], options: dict[str, Any]
) -> dict[str, Any]:
    """Run the workload against the Python port in this process."""
    from fusejs import Fuse

    started = time.perf_counter()
    fuse = Fuse(corpus, options)
    startup_ms = (time.perf_counter() - started) * 1000

    # Warm up, so the measured phase is steady state rather than first-call
    # overhead — the same courtesy the JS side gets from its JIT warmup.
    for query in queries[: min(50, len(queries))]:
        fuse.search(query)

    latencies_ms: list[float] = []
    total_hits = 0
    bench_started = time.perf_counter()
    for query in queries:
        call_started = time.perf_counter()
        results = fuse.search(query)
        latencies_ms.append((time.perf_counter() - call_started) * 1000)
        total_hits += len(results)
    elapsed = time.perf_counter() - bench_started

    return {
        "engine": "fusejs-python",
        "runtime": f"CPython {platform.python_version()}",
        "startup_ms": round(startup_ms, 3),
        "searches": len(queries),
        "total_hits": total_hits,
        "throughput_per_s": round(len(queries) / elapsed, 1),
        "latency_ms": {
            "mean": round(statistics.fmean(latencies_ms), 4),
            "p50": round(percentile(latencies_ms, 0.50), 4),
            "p95": round(percentile(latencies_ms, 0.95), 4),
            "p99": round(percentile(latencies_ms, 0.99), 4),
            "max": round(max(latencies_ms), 4),
        },
        "peak_rss_bytes": peak_rss_bytes(),
    }


def measure_node(
    corpus: list[dict[str, Any]], queries: list[str], options: dict[str, Any]
) -> dict[str, Any]:
    """Run the same workload against fuse.js in a fresh Node process."""
    script = ROOT / "bench" / "bench.mjs"
    payload = json.dumps(
        {"corpus": corpus, "queries": queries, "options": options},
        ensure_ascii=False,
    )
    completed = subprocess.run(
        ["node", str(script)],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        cwd=str(ROOT),
    )
    result: dict[str, Any] = json.loads(completed.stdout)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", type=int, default=1000)
    parser.add_argument("--queries", type=int, default=500)
    parser.add_argument("--out", type=Path, default=Path("bench/results.json"))
    args = parser.parse_args()

    corpus = build_corpus(args.docs)
    queries = build_queries(args.queries)
    options = {
        "keys": ["title", "author.firstName", "author.lastName", "tags"],
        "includeScore": True,
    }

    print(f"corpus  : {len(corpus)} documents, 4 keys")
    print(f"queries : {len(queries)}")
    print()

    python_result = measure_python(corpus, queries, options)
    try:
        node_result = measure_node(corpus, queries, options)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"node benchmark unavailable: {exc}", file=sys.stderr)
        node_result = {"engine": "fuse.js", "error": str(exc)}

    ratio = None
    if "throughput_per_s" in node_result and python_result["throughput_per_s"]:
        ratio = round(
            node_result["throughput_per_s"] / python_result["throughput_per_s"], 2
        )

    report = {
        "workload": {
            "documents": len(corpus),
            "queries": len(queries),
            "keys": options["keys"],
            "corpus_seed": 42,
            "query_seed": 7,
        },
        "environment": {
            "platform": platform.platform(),
            "processor": platform.processor() or "unknown",
            "python": platform.python_version(),
        },
        "results": [python_result, node_result],
        "summary": {
            "fusejs_faster_by": ratio,
            "note": (
                "fuse.js is expected to be faster: V8 JITs the hot Bitap loop, "
                "CPython interprets it. The port's value is ecosystem reach and "
                "behavioural parity, not throughput. See bench/methodology.md."
            ),
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    for entry in report["results"]:
        if "error" in entry:
            print(f"{entry['engine']:16s} unavailable: {entry['error']}")
            continue
        latency = entry["latency_ms"]
        rss = entry.get("peak_rss_bytes")
        rss_text = f"{rss / 1_048_576:.1f} MiB" if rss else "n/a"
        print(f"{entry['engine']:16s} ({entry['runtime']})")
        print(f"  startup     : {entry['startup_ms']:.1f} ms")
        print(f"  throughput  : {entry['throughput_per_s']:.1f} searches/s")
        print(
            f"  latency     : p50 {latency['p50']:.3f} ms  "
            f"p95 {latency['p95']:.3f} ms  p99 {latency['p99']:.3f} ms"
        )
        print(f"  peak RSS    : {rss_text}")
        print()

    if ratio:
        print(f"fuse.js is {ratio}x faster on throughput.")
    print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
