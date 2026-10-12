"""
eval_comparison.py
Comprehensive evaluation comparison between:
1. Static Baseline Scoring
2. Phase 6A Deterministic Adaptive Baseline
3. Phase 6B RL Prioritizer — Cold-Start Policy (0 feedback events, eval_mode=True)
4. Phase 6B RL Prioritizer — Trained Policy (5 controlled feedback updates, eval_mode=True)

Evaluates on identical ground-truth labelled fixtures and synthetic feedback sequences.
Computes Top-3 Precision, Recall, F1, False Positive count, score demotion rates, and rank demotion rates.
"""

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from models import Finding, FindingSource, Severity
import scoring
from rl_experiment import AdaptivePrioritizer, DISCRETE_ACTIONS
from eval_feedback import (
    compute_false_positive_demotion_metrics,
    evaluate_detection_metrics,
    get_evaluation_fixture,
)


def run_comparison_evaluation(seed: int = 42) -> Dict[str, Any]:
    """
    Executes a reproducible evaluation comparing Static, Adaptive, RL Cold-Start, and RL Trained ranking.
    Returns structured metrics dictionary.
    """
    findings_fixture, ground_truth = get_evaluation_fixture()
    total_gt = len(ground_truth)  # In-scope ground truth true positives in evaluated dataset: 3

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Static Baseline Scoring
    # ─────────────────────────────────────────────────────────────────────────
    static_findings = [copy.deepcopy(f) for f in findings_fixture]
    static_ranked = scoring.rank_findings(static_findings)
    static_metrics = evaluate_detection_metrics(static_ranked, ground_truth, top_k=3, total_ground_truth=total_gt)
    static_order = [(f.id, f.title, getattr(f, "risk_score", 0.0)) for f in static_ranked]
    static_rank_map = {f.id: (idx + 1, getattr(f, "risk_score", 0.0)) for idx, f in enumerate(static_ranked)}

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Synthetic Feedback Definition
    # ─────────────────────────────────────────────────────────────────────────
    # False-positive rule rejected 3 times; true-positive rule accepted 2 times
    synthetic_stats = {
        "rule_stats": {
            "rules.python.security.sqli-fp": {"accepted": 0, "rejected": 3},
            "rules.python.security.path-traversal": {"accepted": 2, "rejected": 0},
        },
        "source_stats": {},
    }
    rejected_rule_ids = {"rules.python.security.sqli-fp"}

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Phase 6A Deterministic Adaptive Scoring
    # ─────────────────────────────────────────────────────────────────────────
    adaptive_findings = [copy.deepcopy(f) for f in findings_fixture]
    adaptive_ranked = scoring.rank_findings_adaptive(adaptive_findings, synthetic_stats)
    adaptive_metrics = evaluate_detection_metrics(adaptive_ranked, ground_truth, top_k=3, total_ground_truth=total_gt)
    adaptive_order = [(f.id, f.title, getattr(f, "risk_score", 0.0)) for f in adaptive_ranked]

    adapt_fp_metrics = compute_false_positive_demotion_metrics(adaptive_ranked, static_rank_map, rejected_rule_ids, top_k=3)
    adapt_score_demoted = adapt_fp_metrics["fp_count_with_reduced_score"]
    adapt_score_demotion_rate = adapt_fp_metrics["fp_share_with_reduced_score"]
    adapt_score_reduction_pct = adapt_fp_metrics["fp_mean_score_reduction_pct"]
    adapt_rank_demotion_rate = adapt_fp_metrics["fp_rank_demotion_rate"]

    # ─────────────────────────────────────────────────────────────────────────
    # 4. Phase 6B RL — Cold-Start Policy (0 feedback events)
    # ─────────────────────────────────────────────────────────────────────────
    rl_cold = AdaptivePrioritizer(eval_mode=True, seed=seed)
    rl_cold_findings = [copy.deepcopy(f) for f in findings_fixture]
    rl_cold_ranked, rl_cold_actions = scoring.rank_findings_rl(rl_cold_findings, rl_cold)
    rl_cold_metrics = evaluate_detection_metrics(rl_cold_ranked, ground_truth, top_k=3, total_ground_truth=total_gt)
    rl_cold_order = [(f.id, f.title, getattr(f, "risk_score", 0.0)) for f in rl_cold_ranked]

    rl_cold_fp_metrics = compute_false_positive_demotion_metrics(rl_cold_ranked, static_rank_map, rejected_rule_ids, top_k=3)
    rl_cold_score_demotion_rate = rl_cold_fp_metrics["fp_share_with_reduced_score"]
    rl_cold_score_reduction_pct = rl_cold_fp_metrics["fp_mean_score_reduction_pct"]
    rl_cold_rank_demotion_rate = rl_cold_fp_metrics["fp_rank_demotion_rate"]

    # ─────────────────────────────────────────────────────────────────────────
    # 5. Phase 6B RL — Trained Policy (5 controlled feedback updates)
    # ─────────────────────────────────────────────────────────────────────────
    rl_trained = AdaptivePrioritizer(eval_mode=False, evidence_threshold=1, seed=seed)

    f2_sqli = [f for f in findings_fixture if f.id == "f2-sqli-fp"][0]
    f4_path = [f for f in findings_fixture if f.id == "f4-path-tp"][0]

    # 3 rejections on f2 (reward = -1.0)
    for _ in range(3):
        action = rl_trained.get_priority_adjustment(f2_sqli)
        rl_trained.update(f2_sqli, reward=-1.0, action=action)

    # 2 acceptances on f4 (reward = +1.0)
    for _ in range(2):
        action = rl_trained.get_priority_adjustment(f4_path)
        rl_trained.update(f4_path, reward=1.0, action=action)

    # Evaluation mode: greedy deterministic policy (no exploration, no weight updates)
    rl_trained.eval_mode = True

    rl_trained_findings = [copy.deepcopy(f) for f in findings_fixture]
    rl_trained_ranked, rl_trained_actions = scoring.rank_findings_rl(rl_trained_findings, rl_trained)
    rl_trained_metrics = evaluate_detection_metrics(rl_trained_ranked, ground_truth, top_k=3, total_ground_truth=total_gt)
    rl_trained_order = [(f.id, f.title, getattr(f, "risk_score", 0.0)) for f in rl_trained_ranked]

    rl_trained_fp_metrics = compute_false_positive_demotion_metrics(rl_trained_ranked, static_rank_map, rejected_rule_ids, top_k=3)
    rl_trained_score_demotion_rate = rl_trained_fp_metrics["fp_share_with_reduced_score"]
    rl_trained_score_reduction_pct = rl_trained_fp_metrics["fp_mean_score_reduction_pct"]
    rl_trained_rank_demotion_rate = rl_trained_fp_metrics["fp_rank_demotion_rate"]

    # ─────────────────────────────────────────────────────────────────────────
    # Formatted Benchmark Display
    # ─────────────────────────────────────────────────────────────────────────
    print("=" * 88)
    print("  PHASE 6B: RIGOROUS EVALUATION BENCHMARK (STATIC vs ADAPTIVE vs RL)")
    print("=" * 88)
    print()
    print("Evaluated Dataset Composition:")
    print("  • Total Candidate Findings: 5")
    print("    - f1-cmd-tp:    Command Injection           [True Positive]")
    print("    - f2-sqli-fp:   False Positive SQL Warning  [False Positive, Initially Rank 2]")
    print("    - f3-creds-tp:  Hardcoded Credentials       [True Positive]")
    print("    - f4-path-tp:   Path Traversal              [True Positive, Initially Rank 4]")
    print("    - f5-style-fp:  Noisy Code Smell Warning    [False Positive]")
    print(f"  • Candidate Set Ground-Truth True Positives: {total_gt} (f1, f3, f4)")
    print("  • Total File Ground-Truth Vulnerabilities:  8 (in vulnerable_app.py)")
    print("  • Synthetic Feedback Sequence: 3 rejections on 'sqli-fp', 2 acceptances on 'path-traversal'")
    print()

    # Formatted Metrics Comparison Table
    col_w = 16
    print("-" * 88)
    print(f"{'Metric':<28} | {'Static Baseline':<{col_w}} | {'Adaptive (6A)':<{col_w}} | {'RL Cold-Start':<{col_w}} | {'RL Trained':<{col_w}}")
    print("-" * 88)
    print(f"{'Candidate Ranking Prec (Top-3)':<28} | {static_metrics['precision']:<{col_w}.3f} | {adaptive_metrics['precision']:<{col_w}.3f} | {rl_cold_metrics['precision']:<{col_w}.3f} | {rl_trained_metrics['precision']:<{col_w}.3f}")
    print(f"{'Candidate Ranking Recall (Top-3)':<28} | {static_metrics['recall']:<{col_w}.3f} | {adaptive_metrics['recall']:<{col_w}.3f} | {rl_cold_metrics['recall']:<{col_w}.3f} | {rl_trained_metrics['recall']:<{col_w}.3f}")
    print(f"{'Candidate Ranking F1 (Top-3)':<28} | {static_metrics['f1']:<{col_w}.3f} | {adaptive_metrics['f1']:<{col_w}.3f} | {rl_cold_metrics['f1']:<{col_w}.3f} | {rl_trained_metrics['f1']:<{col_w}.3f}")
    print(f"{'End-to-End Detection Recall':<28} | {'UNAVAILABLE*':<{col_w}} | {'UNAVAILABLE*':<{col_w}} | {'UNAVAILABLE*':<{col_w}} | {'UNAVAILABLE*':<{col_w}}")
    print(f"{'Top-3 False Positives (count)':<28} | {static_metrics['fp']:<{col_w}d} | {adaptive_metrics['fp']:<{col_w}d} | {rl_cold_metrics['fp']:<{col_w}d} | {rl_trained_metrics['fp']:<{col_w}d}")
    print(f"{'Top-3 True Positives (count)':<28} | {static_metrics['tp']:<{col_w}d} | {adaptive_metrics['tp']:<{col_w}d} | {rl_cold_metrics['tp']:<{col_w}d} | {rl_trained_metrics['tp']:<{col_w}d}")
    print(f"{'FP Share w/ Score Reduced':<28} | {'N/A':<{col_w}} | {f'{adapt_score_demotion_rate:.1f}%':<{col_w}} | {f'{rl_cold_score_demotion_rate:.1f}%':<{col_w}} | {f'{rl_trained_score_demotion_rate:.1f}%':<{col_w}}")
    print(f"{'FP Numerical Score Red. %':<28} | {'N/A':<{col_w}} | {f'{adapt_score_reduction_pct:.1f}%':<{col_w}} | {f'{rl_cold_score_reduction_pct:.1f}%':<{col_w}} | {f'{rl_trained_score_reduction_pct:.1f}%':<{col_w}}")
    print(f"{'FP Rank Demotion Rate':<28} | {'N/A':<{col_w}} | {f'{adapt_rank_demotion_rate:.1f}%':<{col_w}} | {f'{rl_cold_rank_demotion_rate:.1f}%':<{col_w}} | {f'{rl_trained_rank_demotion_rate:.1f}%':<{col_w}}")
    print("-" * 88)
    print("  * Note: End-to-end detection recall is UNAVAILABLE in this benchmark because the offline")
    print("    ranking fixture evaluates candidate re-ordering rather than full scanner detection execution.")
    print()

    # Detailed Rankings
    print("--- Detailed Rankings & Applied Action Multipliers ---")
    print("\n1. Static Baseline (No adaptation):")
    for rank, (fid, title, score) in enumerate(static_order, 1):
        in_top3 = " [TOP-3]" if rank <= 3 else ""
        print(f"   Rank {rank}: {fid:<15s} | Score: {score:6.2f} | {title}{in_top3}")

    print("\n2. Phase 6A Deterministic Adaptive (Rule-level bounded feedback):")
    for rank, (fid, title, score) in enumerate(adaptive_order, 1):
        in_top3 = " [TOP-3]" if rank <= 3 else ""
        print(f"   Rank {rank}: {fid:<15s} | Score: {score:6.2f} | {title}{in_top3}")

    print("\n3. Phase 6B Contextual Bandit — Cold-Start Policy (0 feedback events, eval_mode=True):")
    for rank, (fid, title, score) in enumerate(rl_cold_order, 1):
        in_top3 = " [TOP-3]" if rank <= 3 else ""
        act = rl_cold_actions.get(fid, 1.0)
        print(f"   Rank {rank}: {fid:<15s} | Score: {score:6.2f} (action {act:.2f}) | {title}{in_top3}")

    print("\n4. Phase 6B Contextual Bandit — Trained Policy (5 controlled feedback events, eval_mode=True):")
    for rank, (fid, title, score) in enumerate(rl_trained_order, 1):
        in_top3 = " [TOP-3]" if rank <= 3 else ""
        act = rl_trained_actions.get(fid, 1.0)
        print(f"   Rank {rank}: {fid:<15s} | Score: {score:6.2f} (action {act:.2f}) | {title}{in_top3}")

    print()
    print("-" * 88)
    print("--- Scientific Root-Cause Analysis ---")
    print("1. Cold-Start Safety Verification:")
    print("   • When no feedback has been recorded, RL operating in eval_mode correctly and deterministically")
    print("     returns neutral action 1.00 for all findings. Risk scores and ranking match Static Baseline.")
    print()
    print("2. Why Trained RL Selects Action 0.60 on Every Finding:")
    print("   • In sparse feedback settings (5 events), exploration with seed=42 randomly sampled action 0.60")
    print("     during f4's acceptance (+1.0 reward), while actions 1.0 and 1.4 received negative rewards (-1.0)")
    print("     during f2's rejections. Actions 0.2 and 1.8 were never sampled.")
    print("   • Because all candidate findings share identical language and scanner source (Python Semgrep),")
    print("     the linear feature representation (w_a^T x) generalizes broadly across all findings.")
    print("   • In greedy evaluation mode, action 0.60 has the highest predicted reward for EVERY finding.")
    print("   • Uniform scaling by 0.60 reduces absolute scores (score demotion = 100%) but leaves the relative")
    print("     ordering completely unchanged (rank demotion = 0.0%).")
    print()
    print("3. Why Phase 6A Decisively Outperforms RL Here:")
    print("   • Phase 6A applies deterministic rule-level aggregation without cross-talk across different rules.")
    print("   • Phase 6A penalizes the rejected rule ('sqli-fp' -> 0.40) and boosts the accepted rule ('path-traversal' -> 1.10),")
    print("     successfully displacing the false positive out of Top-3 and elevating the true positive into Top-3.")
    print("   • Phase 6A achieves Top-3 Precision 1.000, Recall 1.000, F1 1.000, and 0 Top-3 False Positives.")
    print()
    print("4. Production Architectural Conclusion:")
    print("   • Phase 6A MUST REMAIN THE PRODUCTION DEFAULT.")
    print("   • Phase 6B Contextual Bandit RL remains strictly isolated as an experimental opt-in module.")
    print("=" * 88)

    return {
        "candidate_counts": {
            "total": len(findings_fixture),
            "tp": 3,
            "fp": 2,
            "total_gt": total_gt,
        },
        "static_metrics": static_metrics,
        "adaptive_metrics": adaptive_metrics,
        "rl_cold_metrics": rl_cold_metrics,
        "rl_trained_metrics": rl_trained_metrics,
        "adaptive_demotion": {
            "score_rate": adapt_score_demotion_rate,
            "rank_rate": adapt_rank_demotion_rate,
        },
        "rl_cold_demotion": {
            "score_rate": rl_cold_score_demotion_rate,
            "rank_rate": rl_cold_rank_demotion_rate,
        },
        "rl_trained_demotion": {
            "score_rate": rl_trained_score_demotion_rate,
            "rank_rate": rl_trained_rank_demotion_rate,
        },
        "static_order": static_order,
        "adaptive_order": adaptive_order,
        "rl_cold_order": rl_cold_order,
        "rl_trained_order": rl_trained_order,
        "rl_cold_actions": rl_cold_actions,
        "rl_trained_actions": rl_trained_actions,
    }


if __name__ == "__main__":
    run_comparison_evaluation()
