#!/usr/bin/env python3
"""
perf_summary.py – Fast CLI summary of key paint‑ and navigation‑timings
for Selenium JSON visit logs.

Usage
-----
    python perf_summary.py /path/to/logs
    python perf_summary.py /path/to/logs -o results.csv
"""

from __future__ import annotations
import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


# ────────────────────────── helpers ──────────────────────────────────────────
def _delta(entry: Dict[str, Any], end: str, start: str) -> float | None:
    e, s = entry.get(end, 0), entry.get(start, 0)
    return (e - s) if e and s else None


def _first(items, default=None):
    try:
        return next(iter(items))
    except (StopIteration, TypeError):
        return default


# ────────────────────────── metric extraction ────────────────────────────────
METRIC_KEYS: Tuple[str, ...] = (
    "dns_ms",
    "tcp_ms",
    "tls_ms",
    "ttfb_ms",
    "response_end_ms",
    "load_event_ms",
    "fcp_ms",
    "lcp_ms",
    "transfer_kb",
)


def extract_metrics(path: Path) -> Dict[str, float | None]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)

    nav = data.get("navigation", [{}])[0]
    paints = data.get("paint", [])
    lcps = data.get("largest-contentful-paint", [])

    metrics = {
        "dns_ms": _delta(nav, "domainLookupEnd", "domainLookupStart"),
        "tcp_ms": _delta(nav, "connectEnd", "connectStart"),
        "tls_ms": _delta(nav, "connectEnd", "secureConnectionStart"),
        "ttfb_ms": nav.get("responseStart"),
        "response_end_ms": nav.get("responseEnd"),
        "load_event_ms": nav.get("loadEventEnd"),
        "fcp_ms": _first(
            p["startTime"] for p in paints if p["name"] == "first-contentful-paint"
        ),
        "lcp_ms": max(
            (L.get("renderTime") or L.get("startTime", 0) for L in lcps), default=None
        ),
        "transfer_kb": (nav.get("transferSize") or 0) / 1024,
        "url": nav.get("name", str(path)),
        "file": path.name,
    }
    return metrics


# ────────────────────────── stats & pretty print ─────────────────────────────
ASCII_ART = r"""
FCP (paint)                ↘︎ visual
LCP (largest‑contentful)   ↗︎ milestones users notice

domainLookup  ┐
connect/TLS   ├─→ TTFB ──→ ResponseEnd          ↘︎ network path
payload bytes ┘                     LoadEventEnd ↗︎ browser work
""".strip("\n")


def summarize(values: List[float]) -> Tuple[str, str, str]:
    if not values:
        return ("–", "–", "–")
    mean = f"{statistics.mean(values):.1f}"
    median = f"{statistics.median(values):.1f}"
    stdev = f"{statistics.stdev(values):.1f}" if len(values) > 1 else "0.0"
    return mean, median, stdev


def print_report(all_rows: List[Dict[str, Any]], ascii_art: str = ASCII_ART):
    print("\n" + ascii_art + "\n")
    print(f"Files processed: {len(all_rows)}\n")

    # group values per metric
    bucket: Dict[str, List[float]] = {k: [] for k in METRIC_KEYS}
    for row in all_rows:
        for k in METRIC_KEYS:
            v = row.get(k)
            if v is not None:
                bucket[k].append(v)

    width = 16
    print("Metric".ljust(width), "mean".rjust(8), "median".rjust(8), "stdev".rjust(8))
    print("-" * (width + 26))
    for k in METRIC_KEYS:
        m, med, sd = summarize(bucket[k])
        print(k.ljust(width), m.rjust(8), med.rjust(8), sd.rjust(8))
    print()


# ────────────────────────── main entry point ────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Summarise key paint & navigation metrics from Selenium JSON logs."
    )
    parser.add_argument("root", help="Directory or single JSON file to scan")
    parser.add_argument(
        "-o",
        "--out",
        metavar="CSV",
        help="Optional CSV file to write per‑visit metrics",
    )
    args = parser.parse_args()

    root = Path(args.root)
    json_files = [root] if root.is_file() else list(root.rglob("*.json"))

    if not json_files:
        sys.exit(f"No JSON files found under '{root}'")

    rows = [extract_metrics(p) for p in json_files]

    # optional CSV
    if args.out:
        import csv

        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=["file", "url", *METRIC_KEYS])
            writer.writeheader()
            writer.writerows(rows)
        print(f"Wrote per‑visit metrics to {args.out}")

    # pretty terminal summary
    print_report(rows)


if __name__ == "__main__":
    main()
