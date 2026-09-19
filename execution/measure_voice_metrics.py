"""
measure_voice_metrics.py
Week 8: Evaluate voice pipeline performance.

Reads a test log file (JSONL) with known ground-truth labels and
computes VAD accuracy, false activation rate, missed command rate,
and average response latency.

Usage:
    python execution/measure_voice_metrics.py --log .tmp/voice_test_log.jsonl

Log format (one JSON object per line):
    {
      "transcript": "hey review my code",
      "energy_db": -12.3,
      "ground_truth_command": "review",   // null if background noise
      "recognised_command": "review",     // null if missed
      "latency_ms": 420,
      "was_wake_word": true,
      "was_vad_pass": true
    }
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


def load_log(path: str) -> list[dict]:
    """Load JSONL log file into a list of records."""
    records = []
    with open(path) as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"WARNING: Line {i} is not valid JSON — skipped. ({e})", file=sys.stderr)
    return records


def compute_metrics(records: list[dict]) -> dict:
    """
    Compute evaluation metrics from voice test log.

    Returns a dict with:
      - total_samples
      - true_activations   (wake word + valid command)
      - false_activations  (wake word but no real command)
      - missed_commands    (should have activated but did not)
      - correct_commands   (command correctly parsed)
      - command_accuracy   (correct / total true activations)
      - avg_latency_ms
      - p95_latency_ms
      - false_activation_rate
    """
    total = len(records)
    true_act = 0
    false_act = 0
    missed = 0
    correct = 0
    latencies: list[float] = []

    for r in records:
        gt: Optional[str] = r.get("ground_truth_command")
        recognised: Optional[str] = r.get("recognised_command")
        was_vad = r.get("was_vad_pass", False)
        was_wake = r.get("was_wake_word", False)
        latency: Optional[float] = r.get("latency_ms")

        # ── True activation: gt is a real command, and pipeline activated ──
        if gt is not None and was_vad and was_wake:
            true_act += 1
            if recognised == gt:
                correct += 1
            if latency is not None:
                latencies.append(latency)

        # ── False activation: no real command, but pipeline activated ──────
        elif gt is None and was_vad and was_wake:
            false_act += 1

        # ── Missed command: real command, but pipeline did NOT activate ─────
        elif gt is not None and not (was_vad and was_wake):
            missed += 1

    total_activations = true_act + false_act
    cmd_accuracy = (correct / true_act * 100) if true_act > 0 else 0.0
    far = (false_act / total_activations * 100) if total_activations > 0 else 0.0

    latencies_sorted = sorted(latencies)
    avg_lat = sum(latencies) / len(latencies) if latencies else 0.0
    p95_idx = max(0, int(len(latencies_sorted) * 0.95) - 1)
    p95_lat = latencies_sorted[p95_idx] if latencies_sorted else 0.0

    return {
        "total_samples": total,
        "true_activations": true_act,
        "false_activations": false_act,
        "missed_commands": missed,
        "correct_commands": correct,
        "command_accuracy_pct": round(cmd_accuracy, 2),
        "false_activation_rate_pct": round(far, 2),
        "avg_latency_ms": round(avg_lat, 1),
        "p95_latency_ms": round(p95_lat, 1),
    }


def print_report(metrics: dict) -> None:
    """Pretty-print the metrics as a markdown table."""
    print("\n## Voice Pipeline Evaluation Report\n")
    print(f"| Metric | Value |")
    print(f"|---|---|")
    for k, v in metrics.items():
        label = k.replace("_", " ").title()
        print(f"| {label} | {v} |")
    print()

    # Summary assessment
    far = metrics["false_activation_rate_pct"]
    acc = metrics["command_accuracy_pct"]
    lat = metrics["avg_latency_ms"]

    print("### Assessment")
    print(f"- False activation rate: {'✅ Good' if far < 10 else '⚠ High'} ({far}%)")
    print(f"- Command accuracy: {'✅ Good' if acc >= 80 else '⚠ Needs improvement'} ({acc}%)")
    print(f"- Average latency: {'✅ Fast' if lat < 500 else '⚠ Slow'} ({lat}ms)")


def main(log_path: str) -> None:
    path = Path(log_path)
    if not path.exists():
        print(f"ERROR: Log file not found: {log_path}", file=sys.stderr)
        sys.exit(1)

    records = load_log(log_path)
    if not records:
        print("ERROR: No records found in log file.", file=sys.stderr)
        sys.exit(1)

    metrics = compute_metrics(records)
    print_report(metrics)

    # Also write JSON for downstream processing
    out_path = path.parent / (path.stem + "_metrics.json")
    out_path.write_text(json.dumps(metrics, indent=2))
    print(f"\nMetrics JSON written to: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log",
        required=True,
        help="Path to voice test log JSONL file",
    )
    args = parser.parse_args()

    try:
        main(args.log)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
