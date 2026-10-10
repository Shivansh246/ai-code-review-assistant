"""
eval_feedback.py
Controlled prototype evaluation script for Phase 6A Adaptive Feedback Baseline.

Measures score adjustments, rank shifts, score demotion rate, rank demotion rate,
Top-3 false-positive reduction, Top-K precision/recall/F1, and F1 stability against ground-truth security annotations.
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from models import Finding, FindingSource, Severity
import scoring


def line_overlaps(start1: int, end1: int, start2: int, end2: int) -> bool:
    """Check if two 1-based line ranges overlap."""
    return max(start1, start2) <= min(end1, end2)


def get_evaluation_fixture() -> Tuple[List[Finding], List[dict]]:
    """
    Returns a deterministic candidate findings fixture and ground truth entries.
    Contains both true positive security findings and a false positive candidate initially in Top-3.
    """
    gt_path = Path(__file__).resolve().parent / "test_samples" / "ground_truth.json"
    with open(gt_path, "r", encoding="utf-8") as f:
        ground_truth = json.load(f)

    # Candidate findings detected by scanners
    # 1. True Positive: Command Injection (L28-29 in vulnerable_app.py)
    f1_cmd_tp = Finding(
        id="f1-cmd-tp",
        source=FindingSource.semgrep,
        severity=Severity.critical,
        title="Command Injection",
        description="Subprocess shell=True with user input",
        file="vulnerable_app.py",
        line_start=28,
        line_end=29,
        confidence=0.95,
        rule_id="rules.python.security.command-injection"
    )

    # 2. False Positive: False Positive SQL Warning (L95-97 in vulnerable_app.py, no GT entry) -> Initially Rank 2
    f2_sqli_fp = Finding(
        id="f2-sqli-fp",
        source=FindingSource.semgrep,
        severity=Severity.critical,
        title="False Positive SQL Warning",
        description="Safe ORM call flagged incorrectly as SQLi",
        file="vulnerable_app.py",
        line_start=95,
        line_end=97,
        confidence=0.90,
        rule_id="rules.python.security.sqli-fp"
    )

    # 3. True Positive: Hardcoded Credentials (L11-12 in vulnerable_app.py) -> Initially Rank 3
    f3_creds_tp = Finding(
        id="f3-creds-tp",
        source=FindingSource.semgrep,
        severity=Severity.high,
        title="Hardcoded Credentials",
        description="Plaintext password in source code",
        file="vulnerable_app.py",
        line_start=11,
        line_end=12,
        confidence=0.90,
        rule_id="rules.python.security.hardcoded-credentials"
    )

    # 4. True Positive: Path Traversal (L43-44 in vulnerable_app.py) -> Initially Rank 4
    f4_path_tp = Finding(
        id="f4-path-tp",
        source=FindingSource.semgrep,
        severity=Severity.high,
        title="Path Traversal",
        description="Unsanitized file path construction",
        file="vulnerable_app.py",
        line_start=43,
        line_end=44,
        confidence=0.85,
        rule_id="rules.python.security.path-traversal"
    )

    # 5. False Positive: Noisy Code Smell Warning (L80-82 in vulnerable_app.py) -> Initially Rank 5
    f5_style_fp = Finding(
        id="f5-style-fp",
        source=FindingSource.semgrep,
        severity=Severity.medium,
        title="Noisy Code Smell Warning",
        description="False positive style warning on line 80",
        file="vulnerable_app.py",
        line_start=80,
        line_end=82,
        confidence=0.80,
        rule_id="rules.python.style.noisy-rule"
    )

    findings = [f1_cmd_tp, f2_sqli_fp, f3_creds_tp, f4_path_tp, f5_style_fp]
    return findings, ground_truth


def evaluate_detection_metrics(findings: List[Finding], ground_truth: List[dict], top_k: int = 3) -> Dict[str, float]:
    """
    Computes Precision, Recall, and F1 score for candidate findings evaluated against ground truth.
    Evaluates candidates within the Top-K ranked window.
    """
    top_findings = findings[:top_k]

    tp = 0
    fp = 0
    matched_gt = set()

    for f in top_findings:
        matched = False
        for idx, gt in enumerate(ground_truth):
            if idx in matched_gt:
                continue
            if gt["file"] == f.file and line_overlaps(f.line_start, f.line_end, gt["line_start"], gt["line_end"]):
                matched = True
                matched_gt.add(idx)
                tp += 1
                break
        if not matched:
            fp += 1

    total_gt = len([gt for gt in ground_truth if gt["file"] == "vulnerable_app.py"])
    fn = total_gt - tp

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / total_gt if total_gt > 0 else 0.0
    f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4)
    }


def run_evaluation() -> dict:
    print("=======================================================================")
    print("  PHASE 6A: TRANSPARENT ADAPTIVE FEEDBACK BASELINE EVALUATION (CORRECTED) ")
    print("=======================================================================\n")

    findings, ground_truth = get_evaluation_fixture()

    # 1. Baseline static scoring
    print("--- 1. Baseline Static Scoring ---")
    static_ranked = scoring.rank_findings(findings.copy())
    static_ranks = {}
    for rank_idx, item in enumerate(static_ranked, 1):
        score = scoring.finding_risk_scores.get(item.id, 0.0)
        static_ranks[item.id] = (rank_idx, score)
        print(f"  Rank {rank_idx}: [{item.severity.value.upper():8s}] {item.title:30s} Score: {score:6.2f}")

    baseline_metrics = evaluate_detection_metrics(static_ranked, ground_truth, top_k=3)
    print(f"\n  Baseline Top-3 Metrics -> Precision: {baseline_metrics['precision']:.3f}, Recall: {baseline_metrics['recall']:.3f}, F1: {baseline_metrics['f1']:.3f}\n")

    # 2. Historical feedback simulation
    # User rejects false-positive rule 'rules.python.security.sqli-fp' 3 times
    # User accepts true-positive rule 'rules.python.security.path-traversal' 2 times
    synthetic_stats = {
        "rule_stats": {
            "rules.python.security.sqli-fp": {"accepted": 0, "rejected": 3},
            "rules.python.security.path-traversal": {"accepted": 2, "rejected": 0}
        },
        "source_stats": {}
    }

    print("--- 2. Synthetic Historical Feedback Stats ---")
    print("  Rejected False Positive Rule: 'rules.python.security.sqli-fp' (3 rejections)")
    print("  Accepted True Positive Rule: 'rules.python.security.path-traversal' (2 acceptances)\n")

    # 3. Adaptive scoring & ranking
    print("--- 3. Adaptive Scoring Results ---")
    adaptive_ranked = scoring.rank_findings_adaptive(findings.copy(), synthetic_stats)

    rejected_rule_ids = {"rules.python.security.sqli-fp"}
    total_rejected = len(rejected_rule_ids)

    score_demoted_count = 0
    rank_demoted_count = 0

    for rank_idx, item in enumerate(adaptive_ranked, 1):
        old_rank, old_score = static_ranks[item.id]
        new_score = scoring.finding_risk_scores.get(item.id, 0.0)
        mult = scoring.compute_adaptive_multiplier(item, synthetic_stats)

        rank_str = f"Rank {old_rank} -> {rank_idx}"
        score_str = f"Score {old_score:6.2f} -> {new_score:6.2f} (x{mult:.2f})"
        print(f"  [{item.id:15s}] {item.title:30s} | {rank_str:15s} | {score_str}")

        if item.rule_id in rejected_rule_ids:
            if new_score < old_score:
                score_demoted_count += 1
            if rank_idx > old_rank:
                rank_demoted_count += 1

    score_demotion_rate = (score_demoted_count / total_rejected) * 100.0 if total_rejected > 0 else 0.0
    rank_demotion_rate = (rank_demoted_count / total_rejected) * 100.0 if total_rejected > 0 else 0.0

    top3_fp_before = baseline_metrics["fp"]
    adaptive_metrics = evaluate_detection_metrics(adaptive_ranked, ground_truth, top_k=3)
    top3_fp_after = adaptive_metrics["fp"]
    top3_fp_reduction = top3_fp_before - top3_fp_after

    print(f"\n  Score Demotion Rate:          {score_demotion_rate:.1f}%")
    print(f"  Rank Demotion Rate:           {rank_demotion_rate:.1f}%")
    print(f"  Top-3 False-Positive Reduction: {top3_fp_before} -> {top3_fp_after} (Reduced by {top3_fp_reduction})\n")

    # 4. Adaptive detection metrics
    f1_delta = round(adaptive_metrics["f1"] - baseline_metrics["f1"], 4)

    print("--- 4. Detection Capability & F1 Stability Analysis ---")
    print(f"  Baseline  -> Precision: {baseline_metrics['precision']:.3f} | Recall: {baseline_metrics['recall']:.3f} | F1: {baseline_metrics['f1']:.3f}")
    print(f"  Adaptive  -> Precision: {adaptive_metrics['precision']:.3f} | Recall: {adaptive_metrics['recall']:.3f} | F1: {adaptive_metrics['f1']:.3f}")
    print(f"  F1 Delta  -> {f1_delta:+.3f}")

    # Threshold criterion: F1 delta must be >= -0.05
    F1_STABILITY_THRESHOLD = -0.05
    f1_status = "PASS" if f1_delta >= F1_STABILITY_THRESHOLD else "FAIL"
    print(f"  F1 Stability Status (Threshold >= {F1_STABILITY_THRESHOLD:.2f}): {f1_status}")

    print("\n=======================================================================")
    print(f"  EVALUATION SUMMARY: {f1_status} (PROTOTYPE ENVIRONMENT)")
    print("=======================================================================\n")

    return {
        "baseline_metrics": baseline_metrics,
        "adaptive_metrics": adaptive_metrics,
        "f1_delta": f1_delta,
        "f1_status": f1_status,
        "score_demotion_rate": score_demotion_rate,
        "rank_demotion_rate": rank_demotion_rate,
        "top3_fp_before": top3_fp_before,
        "top3_fp_after": top3_fp_after,
        "top3_fp_reduction": top3_fp_reduction
    }


if __name__ == "__main__":
    run_evaluation()
