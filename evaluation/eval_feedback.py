"""
eval_feedback.py
Controlled prototype evaluation script for Phase 6A Adaptive Feedback Baseline.

Measures score adjustments, rank shifts, score demotion rate, rank demotion rate,
Top-3 false-positive reduction, Top-K precision/recall/F1, and F1 stability against ground-truth security annotations.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from models import Finding, FindingSource, Severity
import scoring


def line_overlaps(start1: int, end1: int, start2: int, end2: int) -> bool:
    """Check if two 1-based line ranges overlap."""
    return max(start1, start2) <= min(end1, end2)


def compute_false_positive_demotion_metrics(
    ranked_findings: List[Finding],
    baseline_rank_map: Dict[str, Tuple[int, float]],
    target_fp_rule_ids: set,
    top_k: int = 3,
) -> Dict[str, Any]:
    """
    Computes unambiguous false-positive mitigation metrics across four distinct concepts:
    1. fp_count_with_reduced_score: Number of target FP findings whose risk score decreased.
    2. fp_share_with_reduced_score: Percentage of target FP findings whose score decreased.
    3. fp_mean_score_reduction_pct: Average numerical percentage reduction in risk score ((old - new) / old * 100%).
    4. fp_rank_demoted_count: Number of target FP findings moved down in rank (new_rank > old_rank).
    5. fp_rank_demotion_rate: Percentage of target FP findings moved down in rank.
    6. top_k_fp_count: Number of target FP findings remaining in the Top-K positions.
    """
    target_fps = [f for f in ranked_findings if f.rule_id in target_fp_rule_ids]
    total_target = len(target_fps)
    if total_target == 0:
        return {
            "fp_count_with_reduced_score": 0,
            "fp_share_with_reduced_score": 0.0,
            "fp_mean_score_reduction_pct": 0.0,
            "fp_rank_demoted_count": 0,
            "fp_rank_demotion_rate": 0.0,
            "top_k_fp_count": 0,
            "score_reductions_detail": {},
        }

    score_decreased_count = 0
    rank_demoted_count = 0
    score_reduction_percentages = []
    top_k_count = 0
    detail = {}

    for rank_idx, f in enumerate(ranked_findings, 1):
        if f.rule_id in target_fp_rule_ids:
            if rank_idx <= top_k:
                top_k_count += 1

            old_rank, old_score = baseline_rank_map.get(f.id, (rank_idx, getattr(f, "risk_score", 0.0)))
            new_score = getattr(f, "risk_score", 0.0)

            if new_score < old_score:
                score_decreased_count += 1

            pct_reduction = ((old_score - new_score) / old_score * 100.0) if old_score > 0 else 0.0
            score_reduction_percentages.append(pct_reduction)

            if rank_idx > old_rank:
                rank_demoted_count += 1

            detail[f.id] = {
                "old_score": old_score,
                "new_score": new_score,
                "score_reduction_pct": round(pct_reduction, 2),
                "old_rank": old_rank,
                "new_rank": rank_idx,
                "rank_demoted": rank_idx > old_rank,
            }

    share_reduced = (score_decreased_count / total_target) * 100.0
    mean_pct_reduction = sum(score_reduction_percentages) / len(score_reduction_percentages) if score_reduction_percentages else 0.0
    rank_demotion_rate = (rank_demoted_count / total_target) * 100.0

    return {
        "fp_count_with_reduced_score": score_decreased_count,
        "fp_share_with_reduced_score": round(share_reduced, 1),
        "fp_mean_score_reduction_pct": round(mean_pct_reduction, 1),
        "fp_rank_demoted_count": rank_demoted_count,
        "fp_rank_demotion_rate": round(rank_demotion_rate, 1),
        "top_k_fp_count": top_k_count,
        "score_reductions_detail": detail,
    }


def evaluate_end_to_end_detection_recall(
    detected_findings: List[Finding],
    all_applicable_ground_truth: List[dict],
) -> Dict[str, Any]:
    """
    Evaluates end-to-end detection recall: ground-truth vulnerabilities actually detected
    divided by all applicable ground-truth vulnerabilities in the target file/project.

    In offline prioritization testbeds where a synthetic candidate fixture is provided
    rather than running the full multi-scanner pipeline, end-to-end detection recall
    is UNAVAILABLE because upstream scanning was not performed across the full file.
    """
    if not all_applicable_ground_truth:
        return {
            "detected_gt_count": 0,
            "total_gt_count": 0,
            "detection_recall": None,
            "status": "UNAVAILABLE: No ground-truth annotations provided",
        }

    matched_gt = set()
    for f in detected_findings:
        for idx, gt in enumerate(all_applicable_ground_truth):
            if idx in matched_gt:
                continue
            if gt.get("file") == f.file and line_overlaps(f.line_start, f.line_end, gt["line_start"], gt["line_end"]):
                matched_gt.add(idx)
                break

    detected_count = len(matched_gt)
    total_count = len(all_applicable_ground_truth)
    recall = round(detected_count / total_count, 4) if total_count > 0 else 0.0

    return {
        "detected_gt_count": detected_count,
        "total_gt_count": total_count,
        "detection_recall": recall,
        "status": "COMPUTED",
    }


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
    # Filter ground truth annotations to those in scope for the evaluated dataset (3 TPs in fixture)
    in_scope_categories = {"command_injection", "hardcoded_credentials", "path_traversal"}
    in_scope_gt = [
        gt for gt in ground_truth
        if gt.get("file") == "vulnerable_app.py" and gt.get("category") in in_scope_categories
    ]
    return findings, in_scope_gt


def evaluate_candidate_ranking_metrics(
    findings: List[Finding],
    ground_truth: List[dict],
    top_k: int = 3,
    total_ground_truth: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Computes Candidate-Set Ranking Precision, Recall, and F1 score for candidate findings.
    Evaluates candidates within the Top-K ranked window.

    Definitions:
    - Candidate Ranking Precision@K: relevant true positives in first K positions / K.
      If len(findings) == 0: precision is 0.0.
      If 0 < len(findings) < K: precision is tp / K (slot capacity precision).
    - Candidate Ranking Recall@K: relevant true positives in first K positions / total ground-truth true positives in the candidate set.
      If total_ground_truth == 0: recall is 0.0.
    - Candidate Ranking F1@K: harmonic mean of Top-K candidate precision and recall (0.0 if precision + recall == 0).
    - Top-K False Positives: false-positive findings placed in the first K positions.
    - End-to-End Detection Recall: explicitly marked UNAVAILABLE on offline ranking fixtures where upstream scanning is not run.
    """
    if top_k <= 0:
        raise ValueError("top_k must be a positive integer")

    if not findings:
        total_gt = int(total_ground_truth) if total_ground_truth is not None else len(ground_truth)
        return {
            "tp": 0,
            "fp": 0,
            "fn": total_gt,
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "candidate_ranking_precision": 0.0,
            "candidate_ranking_recall": 0.0,
            "candidate_ranking_f1": 0.0,
            "end_to_end_detection_recall": None,
            "end_to_end_detection_recall_status": "UNAVAILABLE (offline candidate fixture)",
        }

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

    if total_ground_truth is not None:
        total_gt = int(total_ground_truth)
    else:
        # Match ground-truth annotations in scope for the evaluated findings' files
        eval_files = {f.file for f in findings}
        matched_file_gt = [gt for gt in ground_truth if gt.get("file") in eval_files]
        total_gt = len(matched_file_gt) if matched_file_gt else len(ground_truth)

    fn = max(0, total_gt - tp)

    precision = tp / top_k if top_k > 0 else 0.0
    recall = tp / total_gt if total_gt > 0 else 0.0
    f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

    p_round = round(precision, 4)
    r_round = round(recall, 4)
    f1_round = round(f1, 4)

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": p_round,
        "recall": r_round,
        "f1": f1_round,
        "candidate_ranking_precision": p_round,
        "candidate_ranking_recall": r_round,
        "candidate_ranking_f1": f1_round,
        "end_to_end_detection_recall": None,
        "end_to_end_detection_recall_status": "UNAVAILABLE (offline candidate fixture)",
    }


# Backward-compatible alias
evaluate_detection_metrics = evaluate_candidate_ranking_metrics


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

    baseline_metrics = evaluate_candidate_ranking_metrics(static_ranked, ground_truth, top_k=3)
    print(f"\n  Baseline Top-3 Candidate Ranking -> Precision: {baseline_metrics['precision']:.3f}, Recall: {baseline_metrics['recall']:.3f}, F1: {baseline_metrics['f1']:.3f}")
    print("  End-to-End Detection Recall:        UNAVAILABLE (offline ranking candidate fixture; full scanner pipeline not run)\n")

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

    for rank_idx, item in enumerate(adaptive_ranked, 1):
        old_rank, old_score = static_ranks[item.id]
        new_score = scoring.finding_risk_scores.get(item.id, 0.0)
        mult = scoring.compute_adaptive_multiplier(item, synthetic_stats)

        rank_str = f"Rank {old_rank} -> {rank_idx}"
        score_str = f"Score {old_score:6.2f} -> {new_score:6.2f} (x{mult:.2f})"
        print(f"  [{item.id:15s}] {item.title:30s} | {rank_str:15s} | {score_str}")

    fp_metrics = compute_false_positive_demotion_metrics(adaptive_ranked, static_ranks, rejected_rule_ids, top_k=3)
    adaptive_metrics = evaluate_candidate_ranking_metrics(adaptive_ranked, ground_truth, top_k=3)

    top3_fp_before = baseline_metrics["fp"]
    top3_fp_after = adaptive_metrics["fp"]
    top3_fp_reduction = top3_fp_before - top3_fp_after

    print("\n  False Positive Mitigation Metrics:")
    print(f"  • FP Share with Reduced Score:      {fp_metrics['fp_share_with_reduced_score']:.1f}% ({fp_metrics['fp_count_with_reduced_score']}/1 findings)")
    print(f"  • FP Numerical Score Reduction:     {fp_metrics['fp_mean_score_reduction_pct']:.1f}% magnitude")
    print(f"  • FP Rank Demotion Rate:            {fp_metrics['fp_rank_demotion_rate']:.1f}% ({fp_metrics['fp_rank_demoted_count']}/1 findings)")
    print(f"  • Top-3 False-Positive Reduction:   {top3_fp_before} -> {top3_fp_after} (Reduced by {top3_fp_reduction})\n")

    # 4. Adaptive detection metrics
    f1_delta = round(adaptive_metrics["f1"] - baseline_metrics["f1"], 4)

    print("--- 4. Detection Capability & F1 Stability Analysis ---")
    print(f"  Baseline Candidate Ranking -> Precision: {baseline_metrics['precision']:.3f} | Recall: {baseline_metrics['recall']:.3f} | F1: {baseline_metrics['f1']:.3f}")
    print(f"  Adaptive Candidate Ranking -> Precision: {adaptive_metrics['precision']:.3f} | Recall: {adaptive_metrics['recall']:.3f} | F1: {adaptive_metrics['f1']:.3f}")
    print(f"  F1 Delta                   -> {f1_delta:+.3f}")
    print("  End-to-End Detection Recall:  UNAVAILABLE (ranking adaptation re-orders candidates, does not alter scanner detection)")

    # Threshold criterion: F1 delta must be >= -0.05
    F1_STABILITY_THRESHOLD = -0.05
    f1_status = "PASS" if f1_delta >= F1_STABILITY_THRESHOLD else "FAIL"
    print(f"  F1 Stability Status (Threshold >= {F1_STABILITY_THRESHOLD:.2f}): {f1_status}")

    print("\n=======================================================================")
    print(f"  EVALUATION SUMMARY: {f1_status} (PROTOTYPE ENVIRONMENT)")
    print("=======================================================================")

    return {
        "baseline_metrics": baseline_metrics,
        "adaptive_metrics": adaptive_metrics,
        "f1_delta": f1_delta,
        "f1_status": f1_status,
        "fp_metrics": fp_metrics,
        "score_demotion_rate": fp_metrics["fp_share_with_reduced_score"],
        "rank_demotion_rate": fp_metrics["fp_rank_demotion_rate"],
        "top3_fp_before": top3_fp_before,
        "top3_fp_after": top3_fp_after,
        "top3_fp_reduction": top3_fp_reduction,
    }


if __name__ == "__main__":
    run_evaluation()
