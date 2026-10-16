"""
test_rl_integration.py
Tests for Phase 6B Safe Opt-In RL Backend Integration:
- Feature flag disabled by default (Phase 6A preserved)
- Feature flag enabled applies RL multiplier exactly once
- Bounded scores [0.0, 100.0]
- Safe fallback to Phase 6A on RL exception
- Feedback updates policy only when enabled
- Persistent action attribution across repeated reviews and restarts
- Safe handling of missing/stale action attribution
- Duplicate feedback deduplication
- Three-way scoring comparison interface
"""

import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from models import (
    FeedbackEvent,
    FeedbackStatus,
    Finding,
    FindingSource,
    ReviewRequest,
    Severity,
)
import feedback
import main
import scoring
from rl_experiment import AdaptivePrioritizer, DISCRETE_ACTIONS


class TestRLFeatureFlagAndScoring(unittest.TestCase):
    """Tests for USE_RL_PRIORITIZATION feature flag and scoring behavior."""

    def setUp(self):
        self.finding = Finding(
            id="f-flag-1",
            source=FindingSource.semgrep,
            severity=Severity.critical,
            title="Command Injection",
            description="Dangerous shell command execution",
            file="app.py",
            line_start=10,
            line_end=10,
            confidence=0.95,
            rule_id="rules.python.security.cmdi",
        )

    def test_feature_flag_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(main.is_rl_enabled())

        with patch.dict(os.environ, {"USE_RL_PRIORITIZATION": "0"}):
            self.assertFalse(main.is_rl_enabled())

        with patch.dict(os.environ, {"USE_RL_PRIORITIZATION": "false"}):
            self.assertFalse(main.is_rl_enabled())

    def test_feature_flag_enabled_opt_in(self):
        with patch.dict(os.environ, {"USE_RL_PRIORITIZATION": "1"}):
            self.assertTrue(main.is_rl_enabled())

        with patch.dict(os.environ, {"USE_RL_PRIORITIZATION": "true"}):
            self.assertTrue(main.is_rl_enabled())

    def test_disabled_behavior_matches_phase6a_ranking(self):
        findings = [
            Finding(
                id="f-crit",
                source=FindingSource.semgrep,
                severity=Severity.critical,
                title="Critical Vuln",
                description="desc",
                file="app.py",
                line_start=1,
                line_end=1,
                confidence=1.0,
            ),
            Finding(
                id="f-med",
                source=FindingSource.semgrep,
                severity=Severity.medium,
                title="Med Vuln",
                description="desc",
                file="app.py",
                line_start=2,
                line_end=2,
                confidence=1.0,
            ),
        ]
        # Under Phase 6A, without feedback stats, scores are deterministic base scores
        phase6a_ranked = scoring.rank_findings_adaptive(findings, stats=None)
        self.assertEqual(phase6a_ranked[0].id, "f-crit")
        self.assertEqual(phase6a_ranked[1].id, "f-med")

    def test_rl_multiplier_applied_exactly_once_and_bounded(self):
        p = AdaptivePrioritizer(eval_mode=True, evidence_threshold=1)
        # Train action 1.8 so it becomes greedy choice
        p.update(self.finding, reward=1.0, action=1.8)

        base_score = scoring.score_finding(self.finding)
        rl_score, applied_action = scoring.score_finding_rl(self.finding, p)

        self.assertEqual(applied_action, 1.8)
        expected_score = max(0.0, min(100.0, round(base_score * 1.8, 2)))
        self.assertEqual(rl_score, expected_score)
        self.assertGreaterEqual(rl_score, 0.0)
        self.assertLessEqual(rl_score, 100.0)

    def test_rl_exception_triggers_safe_fallback(self):
        async def run_test():
            with tempfile.TemporaryDirectory() as temp_dir:
                db_path = os.path.join(temp_dir, "test_feedback.db")
                with patch.object(feedback, "DB_PATH", db_path):
                    await feedback.init_db()

                    with patch.dict(os.environ, {"USE_RL_PRIORITIZATION": "1"}):
                        with patch.object(
                            scoring,
                            "rank_findings_rl",
                            side_effect=RuntimeError("Simulated RL failure"),
                        ):
                            with patch("scanner_semgrep.scan_file", return_value=[self.finding]):
                                with patch("scanner_llm.scan_file", return_value=[]):
                                    with patch("scanner_osv.scan_file", return_value=[]):
                                        req = ReviewRequest(file_path="app.py", content="import os")
                                        resp = await main.review_file(req)
                                        self.assertEqual(len(resp.findings), 1)
                                        self.assertEqual(resp.findings[0].id, self.finding.id)

        asyncio.run(run_test())


class TestPersistentActionAttributionAndFeedback(unittest.TestCase):
    """Tests for SQLite-backed action attribution, feedback updates, and deduplication."""

    def test_action_attribution_survives_db_reload(self):
        async def run_test():
            with tempfile.TemporaryDirectory() as temp_dir:
                db_path = os.path.join(temp_dir, "test_attribution.db")
                with patch.object(feedback, "DB_PATH", db_path):
                    await feedback.init_db()

                    # Record action 1.4 for finding-1
                    await feedback.record_rl_action("finding-1", 1.4, review_id="rev-100")

                    # Simulate process restart / reload by reading from fresh connection
                    consumed_action = await feedback.get_and_consume_rl_action("finding-1")
                    self.assertEqual(consumed_action, 1.4)

                    # Subsequent call must return None (already consumed)
                    consumed_again = await feedback.get_and_consume_rl_action("finding-1")
                    self.assertIsNone(consumed_again)

        asyncio.run(run_test())

    def test_repeated_reviews_fifo_action_attribution(self):
        async def run_test():
            with tempfile.TemporaryDirectory() as temp_dir:
                db_path = os.path.join(temp_dir, "test_repeated.db")
                with patch.object(feedback, "DB_PATH", db_path):
                    await feedback.init_db()

                    # Review 1 records 1.4
                    await feedback.record_rl_action("finding-repeat", 1.4, review_id="rev-1")
                    # Review 2 records 0.6
                    await feedback.record_rl_action("finding-repeat", 0.6, review_id="rev-2")

                    # First feedback consumes Review 1's action
                    act1 = await feedback.get_and_consume_rl_action("finding-repeat")
                    self.assertEqual(act1, 1.4)

                    # Second feedback consumes Review 2's action
                    act2 = await feedback.get_and_consume_rl_action("finding-repeat")
                    self.assertEqual(act2, 0.6)

                    # Third feedback has no remaining unconsumed action
                    act3 = await feedback.get_and_consume_rl_action("finding-repeat")
                    self.assertIsNone(act3)

        asyncio.run(run_test())

    def test_matching_by_review_id(self):
        async def run_test():
            with tempfile.TemporaryDirectory() as temp_dir:
                db_path = os.path.join(temp_dir, "test_rev_id.db")
                with patch.object(feedback, "DB_PATH", db_path):
                    await feedback.init_db()

                    await feedback.record_rl_action("f-1", 1.4, review_id="rev-alpha")
                    await feedback.record_rl_action("f-1", 0.6, review_id="rev-beta")

                    # Query specifically for rev-beta
                    act_beta = await feedback.get_and_consume_rl_action("f-1", review_id="rev-beta")
                    self.assertEqual(act_beta, 0.6)

                    # Query specifically for rev-alpha
                    act_alpha = await feedback.get_and_consume_rl_action("f-1", review_id="rev-alpha")
                    self.assertEqual(act_alpha, 1.4)

        asyncio.run(run_test())

    def test_feedback_updates_policy_only_when_enabled(self):
        async def run_test():
            with tempfile.TemporaryDirectory() as temp_dir:
                db_path = os.path.join(temp_dir, "test_fb_enabled.db")
                with patch.object(feedback, "DB_PATH", db_path):
                    await feedback.init_db()

                    f = Finding(
                        id="f-test-policy",
                        source=FindingSource.semgrep,
                        severity=Severity.high,
                        title="Vulnerability",
                        description="desc",
                        file="app.py",
                        line_start=1,
                        line_end=1,
                        confidence=0.9,
                    )
                    await feedback.cache_finding(f)
                    await feedback.record_rl_action("f-test-policy", 1.8)

                    initial_update_count = main.prioritizer.update_count

                    # Feedback with RL disabled
                    with patch.dict(os.environ, {"USE_RL_PRIORITIZATION": "0"}):
                        ev = FeedbackEvent(
                            finding_id="f-test-policy",
                            status=FeedbackStatus.accepted,
                            timestamp="2026-10-12T00:00:00Z",
                        )
                        await main.submit_feedback(ev)
                        # Must NOT update policy
                        self.assertEqual(main.prioritizer.update_count, initial_update_count)

                    # Action was NOT consumed because RL was disabled! Now enable RL:
                    with patch.dict(os.environ, {"USE_RL_PRIORITIZATION": "1"}):
                        ev2 = FeedbackEvent(
                            finding_id="f-test-policy",
                            status=FeedbackStatus.accepted,
                            timestamp="2026-10-12T00:01:00Z",
                        )
                        await main.submit_feedback(ev2)
                        # Policy MUST now be updated!
                        self.assertEqual(main.prioritizer.update_count, initial_update_count + 1)

        asyncio.run(run_test())

    def test_duplicate_feedback_does_not_double_update(self):
        async def run_test():
            with tempfile.TemporaryDirectory() as temp_dir:
                db_path = os.path.join(temp_dir, "test_dedup.db")
                with patch.object(feedback, "DB_PATH", db_path):
                    await feedback.init_db()

                    f = Finding(
                        id="f-dedup",
                        source=FindingSource.semgrep,
                        severity=Severity.high,
                        title="Vulnerability",
                        description="desc",
                        file="app.py",
                        line_start=1,
                        line_end=1,
                        confidence=0.9,
                    )
                    await feedback.cache_finding(f)
                    await feedback.record_rl_action("f-dedup", 1.4)

                    with patch.dict(os.environ, {"USE_RL_PRIORITIZATION": "1"}):
                        count_before = main.prioritizer.update_count
                        ev = FeedbackEvent(
                            finding_id="f-dedup",
                            status=FeedbackStatus.accepted,
                            timestamp="2026-10-12T00:00:00Z",
                        )
                        # First submission: updates policy
                        await main.submit_feedback(ev)
                        self.assertEqual(main.prioritizer.update_count, count_before + 1)

                        # Duplicate submission: action was already consumed, so skipped
                        await main.submit_feedback(ev)
                        self.assertEqual(main.prioritizer.update_count, count_before + 1)

        asyncio.run(run_test())

    def test_missing_or_stale_action_skips_rl_update_safely(self):
        async def run_test():
            with tempfile.TemporaryDirectory() as temp_dir:
                db_path = os.path.join(temp_dir, "test_missing.db")
                with patch.object(feedback, "DB_PATH", db_path):
                    await feedback.init_db()

                    f = Finding(
                        id="f-unassociated",
                        source=FindingSource.semgrep,
                        severity=Severity.high,
                        title="Vulnerability",
                        description="desc",
                        file="app.py",
                        line_start=1,
                        line_end=1,
                        confidence=0.9,
                    )
                    await feedback.cache_finding(f)
                    # No record_rl_action was called!

                    with patch.dict(os.environ, {"USE_RL_PRIORITIZATION": "1"}):
                        count_before = main.prioritizer.update_count
                        w_10_before = dict(main.prioritizer.action_weights["1.0"])
                        ev = FeedbackEvent(
                            finding_id="f-unassociated",
                            status=FeedbackStatus.accepted,
                            timestamp="2026-10-12T00:00:00Z",
                        )
                        resp = await main.submit_feedback(ev)
                        self.assertEqual(resp["status"], "recorded")
                        # Update count and weights must be completely unchanged
                        self.assertEqual(main.prioritizer.update_count, count_before)
                        self.assertEqual(main.prioritizer.action_weights["1.0"], w_10_before)

        asyncio.run(run_test())

    def test_client_spoofed_action_ignored_server_action_authoritative(self):
        async def run_test():
            with tempfile.TemporaryDirectory() as temp_dir:
                db_path = os.path.join(temp_dir, "test_spoof.db")
                with patch.object(feedback, "DB_PATH", db_path):
                    await feedback.init_db()

                    f = Finding(
                        id="f-spoof-target",
                        source=FindingSource.semgrep,
                        severity=Severity.high,
                        title="Vulnerability",
                        description="desc",
                        file="app.py",
                        line_start=1,
                        line_end=1,
                        confidence=0.9,
                    )
                    await feedback.cache_finding(f)
                    # Authoritative server recorded action is 0.6
                    await feedback.record_rl_action("f-spoof-target", 0.6, review_id="rev-auth")

                    with patch.dict(os.environ, {"USE_RL_PRIORITIZATION": "1"}):
                        with patch.object(main.prioritizer, "update") as mock_update:
                            # Client attempts to spoof action with 1.8
                            ev = FeedbackEvent(
                                finding_id="f-spoof-target",
                                status=FeedbackStatus.accepted,
                                timestamp="2026-10-12T00:00:00Z",
                                review_id="rev-auth",
                                applied_action=1.8,
                            )
                            await main.submit_feedback(ev)
                            # Must have called prioritizer.update with authoritative server action 0.6, NOT 1.8!
                            mock_update.assert_called_once()
                            _, kwargs = mock_update.call_args
                            self.assertEqual(kwargs.get("action"), 0.6)

        asyncio.run(run_test())

    def test_review_id_propagated_to_all_findings(self):
        async def run_test():
            with tempfile.TemporaryDirectory() as temp_dir:
                db_path = os.path.join(temp_dir, "test_rev_prop.db")
                with patch.object(feedback, "DB_PATH", db_path):
                    await feedback.init_db()

                    f = Finding(
                        id="f-prop",
                        source=FindingSource.semgrep,
                        severity=Severity.medium,
                        title="Finding",
                        description="desc",
                        file="app.py",
                        line_start=10,
                        line_end=10,
                        confidence=0.8,
                    )
                    with patch("scanner_semgrep.scan_file", return_value=[f]):
                        with patch("scanner_llm.scan_file", return_value=[]):
                            with patch("scanner_osv.scan_file", return_value=[]):
                                req = ReviewRequest(file_path="app.py", content="x = 1")
                                resp = await main.review_file(req)
                                self.assertEqual(len(resp.findings), 1)
                                self.assertEqual(resp.findings[0].review_id, resp.request_id)
                                self.assertIsNotNone(resp.findings[0].risk_score)

        asyncio.run(run_test())


class TestEvaluationComparisonInterface(unittest.TestCase):
    """Tests for the three-way compare_scoring_methods evaluation interface."""

    def test_compare_scoring_methods_interface(self):
        findings = [
            Finding(
                id="f1",
                source=FindingSource.semgrep,
                severity=Severity.critical,
                title="Vuln 1",
                description="desc",
                file="app.py",
                line_start=1,
                line_end=1,
                confidence=1.0,
                rule_id="r1",
            ),
            Finding(
                id="f2",
                source=FindingSource.semgrep,
                severity=Severity.high,
                title="Vuln 2",
                description="desc",
                file="app.py",
                line_start=2,
                line_end=2,
                confidence=1.0,
                rule_id="r2",
            ),
        ]
        p = AdaptivePrioritizer(eval_mode=True)
        res = scoring.compare_scoring_methods(findings, feedback_stats=None, prioritizer=p)

        self.assertIn("static", res)
        self.assertIn("adaptive", res)
        self.assertIn("rl", res)

        self.assertEqual(len(res["static"]), 2)
        self.assertEqual(len(res["adaptive"]), 2)
        self.assertEqual(len(res["rl"]), 2)

        # Verify each method populated risk_score directly on each finding
        self.assertIsNotNone(res["static"][0].risk_score)
        self.assertIsNotNone(res["adaptive"][0].risk_score)
        self.assertIsNotNone(res["rl"][0].risk_score)

    def test_eval_comparison_execution(self):
        import sys
        from pathlib import Path
        eval_dir = str(Path(__file__).resolve().parent.parent / "evaluation")
        if eval_dir not in sys.path:
            sys.path.insert(0, eval_dir)
        import eval_comparison

        results = eval_comparison.run_comparison_evaluation(seed=42)
        self.assertIn("static_metrics", results)
        self.assertIn("adaptive_metrics", results)
        self.assertIn("rl_cold_metrics", results)
        self.assertIn("rl_trained_metrics", results)
        self.assertIn("adaptive_demotion", results)
        self.assertIn("rl_cold_demotion", results)
        self.assertIn("rl_trained_demotion", results)

        # Candidate counts and labels
        self.assertEqual(results["candidate_counts"]["total"], 5)
        self.assertEqual(results["candidate_counts"]["tp"], 3)
        self.assertEqual(results["candidate_counts"]["fp"], 2)
        self.assertEqual(results["candidate_counts"]["total_gt"], 3)

        # Adaptive achieves 1.000 F1 and 0 FPs
        self.assertEqual(results["adaptive_demotion"]["rank_rate"], 100.0)
        self.assertEqual(results["adaptive_metrics"]["fp"], 0)
        self.assertEqual(results["adaptive_metrics"]["precision"], 1.0)
        self.assertEqual(results["adaptive_metrics"]["recall"], 1.0)
        self.assertEqual(results["adaptive_metrics"]["f1"], 1.0)

        # RL Cold-Start returns action 1.0 for all findings
        for act in results["rl_cold_actions"].values():
            self.assertEqual(act, 1.0)

        # RL Trained selects action 0.60 for all findings
        for act in results["rl_trained_actions"].values():
            self.assertEqual(act, 0.6)


class TestMetricCalculationAuditAndEdgeCases(unittest.TestCase):
    """
    Automated tests auditing ground-truth metric calculations, hand-calculated examples,
    and edge-case handling (empty candidates, 0 positive labels, fewer than 3 candidates).
    """

    def setUp(self):
        sys_path_orig = list(sys.path)
        eval_dir = str(Path(__file__).resolve().parent.parent / "evaluation")
        if eval_dir not in sys.path:
            sys.path.insert(0, eval_dir)
        import eval_feedback
        self.eval_feedback = eval_feedback

    def test_hand_calculated_metric_calculation_and_inconsistency_catcher(self):
        """
        Hand-calculated benchmark on 5 candidate findings (3 TPs, 2 FPs) and 3 Ground-Truth TPs.
        Directly catches the previous inconsistency where recall was miscalculated against
        an unrelated 8-vulnerability file denominator.
        """
        # Ground truth: 3 vulnerabilities
        gt = [
            {"file": "app.py", "line_start": 10, "line_end": 12, "category": "cmdi"},
            {"file": "app.py", "line_start": 20, "line_end": 22, "category": "creds"},
            {"file": "app.py", "line_start": 30, "line_end": 32, "category": "path"},
        ]
        # 5 candidates: 3 TPs, 2 FPs
        f1_tp = Finding(
            id="f1", source=FindingSource.semgrep, severity=Severity.critical,
            title="Cmd Injection", description="desc", file="app.py",
            line_start=10, line_end=12, confidence=1.0, rule_id="cmdi"
        )
        f2_fp = Finding(
            id="f2", source=FindingSource.semgrep, severity=Severity.critical,
            title="SQLi FP", description="desc", file="app.py",
            line_start=90, line_end=92, confidence=1.0, rule_id="sqli"
        )
        f3_tp = Finding(
            id="f3", source=FindingSource.semgrep, severity=Severity.high,
            title="Creds", description="desc", file="app.py",
            line_start=20, line_end=22, confidence=1.0, rule_id="creds"
        )
        f4_tp = Finding(
            id="f4", source=FindingSource.semgrep, severity=Severity.high,
            title="Path", description="desc", file="app.py",
            line_start=30, line_end=32, confidence=1.0, rule_id="path"
        )
        f5_fp = Finding(
            id="f5", source=FindingSource.semgrep, severity=Severity.medium,
            title="Style FP", description="desc", file="app.py",
            line_start=80, line_end=82, confidence=1.0, rule_id="style"
        )

        # Baseline Top-3: [f1 (TP), f2 (FP), f3 (TP)]
        baseline_ranking = [f1_tp, f2_fp, f3_tp, f4_tp, f5_fp]
        m_base = self.eval_feedback.evaluate_detection_metrics(baseline_ranking, gt, top_k=3, total_ground_truth=3)

        self.assertEqual(m_base["tp"], 2)
        self.assertEqual(m_base["fp"], 1)
        self.assertEqual(m_base["fn"], 1)
        self.assertAlmostEqual(m_base["precision"], 2 / 3, places=3)
        # Catches the previous bug: Recall must be 2/3 (0.667), NOT 2/8 (0.250)!
        self.assertAlmostEqual(m_base["recall"], 2 / 3, places=3)
        self.assertNotAlmostEqual(m_base["recall"], 0.250, places=3)
        # F1 must be 2/3 (0.667), NOT 0.364!
        self.assertAlmostEqual(m_base["f1"], 2 / 3, places=3)
        self.assertNotAlmostEqual(m_base["f1"], 0.364, places=3)

        # Adaptive Top-3: [f1 (TP), f4 (TP), f3 (TP)]
        adaptive_ranking = [f1_tp, f4_tp, f3_tp, f5_fp, f2_fp]
        m_adapt = self.eval_feedback.evaluate_detection_metrics(adaptive_ranking, gt, top_k=3, total_ground_truth=3)

        self.assertEqual(m_adapt["tp"], 3)
        self.assertEqual(m_adapt["fp"], 0)
        self.assertEqual(m_adapt["fn"], 0)
        self.assertEqual(m_adapt["precision"], 1.0)
        # Catches the previous bug: Recall must be 3/3 (1.0), NOT 3/8 (0.375)!
        self.assertEqual(m_adapt["recall"], 1.0)
        self.assertNotAlmostEqual(m_adapt["recall"], 0.375, places=3)
        # F1 must be 1.0, NOT 0.545!
        self.assertEqual(m_adapt["f1"], 1.0)
        self.assertNotAlmostEqual(m_adapt["f1"], 0.545, places=3)

    def test_metric_calculation_edge_cases(self):
        """Tests explicitly handling empty datasets, zero positive labels, and fewer than 3 candidates."""
        gt = [{"file": "app.py", "line_start": 10, "line_end": 12, "category": "cmdi"}]

        # 1. Empty findings
        m_empty = self.eval_feedback.evaluate_detection_metrics([], gt, top_k=3)
        self.assertEqual(m_empty["tp"], 0)
        self.assertEqual(m_empty["fp"], 0)
        self.assertEqual(m_empty["precision"], 0.0)
        self.assertEqual(m_empty["recall"], 0.0)
        self.assertEqual(m_empty["f1"], 0.0)

        # 2. Zero positive labels in dataset
        f_single = Finding(
            id="f1", source=FindingSource.semgrep, severity=Severity.low,
            title="Warning", description="desc", file="app.py",
            line_start=10, line_end=12, confidence=1.0
        )
        m_zero_gt = self.eval_feedback.evaluate_detection_metrics([f_single], [], top_k=3, total_ground_truth=0)
        self.assertEqual(m_zero_gt["tp"], 0)
        self.assertEqual(m_zero_gt["fp"], 1)
        self.assertEqual(m_zero_gt["recall"], 0.0)
        self.assertEqual(m_zero_gt["f1"], 0.0)

        # 3. Fewer than 3 candidates (e.g. 2 candidates, 1 TP, 1 FP)
        f_tp = Finding(
            id="ftp", source=FindingSource.semgrep, severity=Severity.high,
            title="TP", description="desc", file="app.py",
            line_start=10, line_end=12, confidence=1.0
        )
        f_fp = Finding(
            id="ffp", source=FindingSource.semgrep, severity=Severity.high,
            title="FP", description="desc", file="app.py",
            line_start=50, line_end=52, confidence=1.0
        )
        m_few = self.eval_feedback.evaluate_detection_metrics([f_tp, f_fp], gt, top_k=3, total_ground_truth=1)
        self.assertEqual(m_few["tp"], 1)
        self.assertEqual(m_few["fp"], 1)
        # Precision: 1 TP / 3 slots = 0.3333
        self.assertAlmostEqual(m_few["precision"], 1 / 3, places=3)
        # Recall: 1 TP / 1 GT = 1.0
        self.assertEqual(m_few["recall"], 1.0)
        # F1: 2 * (1/3) * 1.0 / (1/3 + 1.0) = (2/3) / (4/3) = 0.5
        self.assertAlmostEqual(m_few["f1"], 0.5, places=3)

        # 4. Invalid top_k
        with self.assertRaises(ValueError):
            self.eval_feedback.evaluate_detection_metrics([f_tp], gt, top_k=0)

    def test_ranking_recall_vs_detection_recall_distinction(self):
        """
        Verifies the conceptual and numerical distinction between:
        1. Candidate-set ranking recall: relevant findings placed in Top-K / candidate TPs (e.g. 2/3 -> 3/3).
        2. End-to-end detection recall: ground-truth vulnerabilities detected / total ground truth (e.g. 3/8).
        Also verifies ranking adaptation reorders findings without altering scanner detection coverage.
        """
        # 8 file-level ground-truth annotations (e.g., vulnerable_app.py)
        full_file_gt = [
            {"file": "app.py", "line_start": 10, "line_end": 12, "category": "cmdi"},
            {"file": "app.py", "line_start": 20, "line_end": 22, "category": "creds"},
            {"file": "app.py", "line_start": 30, "line_end": 32, "category": "path"},
            {"file": "app.py", "line_start": 40, "line_end": 42, "category": "sqli"},
            {"file": "app.py", "line_start": 50, "line_end": 52, "category": "deserial"},
            {"file": "app.py", "line_start": 60, "line_end": 62, "category": "crypto"},
            {"file": "app.py", "line_start": 70, "line_end": 72, "category": "ssrf"},
            {"file": "app.py", "line_start": 80, "line_end": 82, "category": "xss"},
        ]
        self.assertEqual(len(full_file_gt), 8)

        # 5 candidate findings: only 3 TPs detected by scanner (cmdi, creds, path)
        candidate_tp_cmdi = Finding(
            id="f1", source=FindingSource.semgrep, severity=Severity.critical,
            title="Cmd", description="", file="app.py", line_start=10, line_end=12, confidence=1.0
        )
        candidate_fp_sqli = Finding(
            id="f2", source=FindingSource.semgrep, severity=Severity.critical,
            title="SQL FP", description="", file="app.py", line_start=95, line_end=97, confidence=1.0,
            rule_id="sqli-fp"
        )
        candidate_tp_creds = Finding(
            id="f3", source=FindingSource.semgrep, severity=Severity.high,
            title="Creds", description="", file="app.py", line_start=20, line_end=22, confidence=1.0
        )
        candidate_tp_path = Finding(
            id="f4", source=FindingSource.semgrep, severity=Severity.high,
            title="Path", description="", file="app.py", line_start=30, line_end=32, confidence=1.0
        )
        candidate_fp_style = Finding(
            id="f5", source=FindingSource.semgrep, severity=Severity.medium,
            title="Style", description="", file="app.py", line_start=85, line_end=87, confidence=1.0
        )
        all_candidates = [candidate_tp_cmdi, candidate_fp_sqli, candidate_tp_creds, candidate_tp_path, candidate_fp_style]

        # 1. End-to-end scanner detection recall: exactly 3 of 8 detected
        det_eval = self.eval_feedback.evaluate_end_to_end_detection_recall(all_candidates, full_file_gt)
        self.assertEqual(det_eval["detected_gt_count"], 3)
        self.assertEqual(det_eval["total_gt_count"], 8)
        self.assertAlmostEqual(det_eval["detection_recall"], 3 / 8, places=3)
        self.assertEqual(det_eval["status"], "COMPUTED")

        # 2. Candidate ranking recall on candidate slice (3 TPs in fixture)
        in_scope_gt = full_file_gt[:3]  # cmdi, creds, path
        baseline_order = [candidate_tp_cmdi, candidate_fp_sqli, candidate_tp_creds, candidate_tp_path, candidate_fp_style]
        adaptive_order = [candidate_tp_cmdi, candidate_tp_path, candidate_tp_creds, candidate_fp_style, candidate_fp_sqli]

        m_base = self.eval_feedback.evaluate_candidate_ranking_metrics(baseline_order, in_scope_gt, top_k=3)
        m_adapt = self.eval_feedback.evaluate_candidate_ranking_metrics(adaptive_order, in_scope_gt, top_k=3)

        # Baseline Top-3 places 2 of 3 candidate TPs -> recall 2/3 (0.667)
        self.assertAlmostEqual(m_base["candidate_ranking_recall"], 2 / 3, places=3)
        # Adaptive Top-3 places 3 of 3 candidate TPs -> recall 3/3 (1.000)
        self.assertEqual(m_adapt["candidate_ranking_recall"], 1.0)

        # Crucial check: Candidate ranking metrics explicitly set end_to_end_detection_recall to None
        self.assertIsNone(m_base["end_to_end_detection_recall"])
        self.assertIsNone(m_adapt["end_to_end_detection_recall"])

    def test_false_positive_score_demotion_terminology_and_calculations(self):
        """
        Audits and tests false-positive score demotion terminology using the reported 81.00 -> 48.60 example.
        Explicitly distinguishes:
        1. Percentage of false-positive findings whose scores decreased (100.0%).
        2. Numerical percentage reduction in score (40.0%, NOT 100.0%).
        3. Percentage of false-positive findings moved down in rank (0.0%).
        4. Number of false positives remaining in Top-K (1).
        """
        # Baseline finding: f2 at rank 2 with score 81.00
        baseline_ranks = {"f2-sqli-fp": (2, 81.00)}
        target_rule_ids = {"rules.python.security.sqli-fp"}

        # RL Trained case: score decreased from 81.00 to 48.60, rank stayed at 2
        f2_rl = Finding(
            id="f2-sqli-fp", source=FindingSource.semgrep, severity=Severity.critical,
            title="SQL FP", description="", file="app.py", line_start=95, line_end=97, confidence=0.9,
            rule_id="rules.python.security.sqli-fp"
        )
        f2_rl.risk_score = 48.60

        f1_other = Finding(id="f1", source=FindingSource.semgrep, severity=Severity.critical, title="", description="", file="app.py", line_start=1, line_end=2, confidence=1.0)
        f1_other.risk_score = 51.30
        f3_other = Finding(id="f3", source=FindingSource.semgrep, severity=Severity.high, title="", description="", file="app.py", line_start=10, line_end=12, confidence=1.0)
        f3_other.risk_score = 38.88

        # RL ranking: [f1 (rank 1), f2 (rank 2), f3 (rank 3)]
        rl_ranked = [f1_other, f2_rl, f3_other]

        metrics_rl = self.eval_feedback.compute_false_positive_demotion_metrics(
            rl_ranked, baseline_ranks, target_rule_ids, top_k=3
        )

        # 1. Score decreased count & share: 1/1 = 100.0%
        self.assertEqual(metrics_rl["fp_count_with_reduced_score"], 1)
        self.assertEqual(metrics_rl["fp_share_with_reduced_score"], 100.0)

        # 2. Numerical score reduction: (81.00 - 48.60) / 81.00 = 32.40 / 81.00 = 40.0%
        self.assertAlmostEqual(metrics_rl["fp_mean_score_reduction_pct"], 40.0, places=1)
        # Catches the misinterpretation bug: reduction is 40.0%, NOT 100.0%!
        self.assertNotEqual(metrics_rl["fp_mean_score_reduction_pct"], 100.0)

        # 3. Rank demoted count & rate: Rank 2 -> 2 is NOT demoted!
        self.assertEqual(metrics_rl["fp_rank_demoted_count"], 0)
        self.assertEqual(metrics_rl["fp_rank_demotion_rate"], 0.0)

        # 4. Top-3 FP count: still in Top-3!
        self.assertEqual(metrics_rl["top_k_fp_count"], 1)

        # Contrast with Phase 6A Adaptive: score dropped to 32.40 (60% reduction), rank dropped to 5
        f2_adapt = Finding(
            id="f2-sqli-fp", source=FindingSource.semgrep, severity=Severity.critical,
            title="SQL FP", description="", file="app.py", line_start=95, line_end=97, confidence=0.9,
            rule_id="rules.python.security.sqli-fp"
        )
        f2_adapt.risk_score = 32.40
        adapt_ranked = [f1_other, f3_other, f2_adapt]  # f2 now at rank 3 or below

        metrics_adapt = self.eval_feedback.compute_false_positive_demotion_metrics(
            adapt_ranked, baseline_ranks, target_rule_ids, top_k=2  # Top-2 slot: f2 is at rank 3, outside Top-2
        )
        self.assertEqual(metrics_adapt["fp_share_with_reduced_score"], 100.0)
        # Numerical reduction: (81.00 - 32.40) / 81.00 = 60.0%
        self.assertAlmostEqual(metrics_adapt["fp_mean_score_reduction_pct"], 60.0, places=1)
        # Rank demotion: rank 2 -> rank 3 is demoted
        self.assertEqual(metrics_adapt["fp_rank_demoted_count"], 1)
        self.assertEqual(metrics_adapt["fp_rank_demotion_rate"], 100.0)
        # Out of Top-2: count is 0
        self.assertEqual(metrics_adapt["top_k_fp_count"], 0)


class TestRLColdStartAndLearnedStateEvaluation(unittest.TestCase):
    """
    Automated regression tests verifying:
    1. A new policy with no feedback returns neutral 1.0 in eval_mode.
    2. A policy loaded from persisted state is deterministic and does not explore.
    3. Controlled training feedback explains action selection and feature generalization.
    """

    def setUp(self):
        self.finding = Finding(
            id="f-audit-1",
            source=FindingSource.semgrep,
            severity=Severity.critical,
            title="Cmd Injection",
            description="desc",
            file="app.py",
            line_start=10,
            line_end=12,
            confidence=0.95,
            rule_id="rules.security.cmdi"
        )

    def test_new_policy_cold_start_returns_neutral_action_1_0(self):
        """Unlearned policy in eval_mode must return neutral action 1.0 for all findings."""
        p = AdaptivePrioritizer(eval_mode=True)
        self.assertTrue(p.is_cold_start)
        self.assertEqual(p.update_count, 0)

        action = p.get_priority_adjustment(self.finding)
        self.assertEqual(action, 1.0)

        rl_score, applied_action = scoring.score_finding_rl(self.finding, p)
        base_score = scoring.score_finding(self.finding)
        self.assertEqual(applied_action, 1.0)
        self.assertEqual(rl_score, base_score)

    def test_persisted_policy_roundtrip_eval_mode_determinism(self):
        """Persisted policy loaded in eval_mode is deterministic, does not explore, and does not update weights."""
        with tempfile.TemporaryDirectory() as temp_dir:
            weights_file = os.path.join(temp_dir, "weights_audit.json")

            # Train and save a policy
            p_source = AdaptivePrioritizer(eval_mode=False, evidence_threshold=1, seed=42)
            p_source.update(self.finding, reward=1.0, action=1.8)
            p_source.save(weights_file)

            # Load into a clean prioritizer in eval_mode
            p_loaded = AdaptivePrioritizer(eval_mode=True)
            p_loaded.load(weights_file)

            self.assertFalse(p_loaded.is_cold_start)
            self.assertEqual(p_loaded.update_count, 1)

            # Multiple evaluations must be 100% deterministic
            act1 = p_loaded.get_priority_adjustment(self.finding)
            act2 = p_loaded.get_priority_adjustment(self.finding)
            self.assertEqual(act1, act2)
            self.assertEqual(act1, 1.8)
            self.assertEqual(p_loaded.update_count, 1)  # No weight mutation

    def test_controlled_feedback_feature_cross_talk_explanation(self):
        """
        Verifies why trained RL selects 0.60 on all findings:
        Linear feature weights generalize positive reward from action 0.60 across all similar findings.
        """
        p = AdaptivePrioritizer(eval_mode=False, evidence_threshold=1, seed=42)

        f_reject = Finding(
            id="f-rej", source=FindingSource.semgrep, severity=Severity.critical,
            title="SQLi FP", description="desc", file="app.py",
            line_start=95, line_end=97, confidence=0.90, rule_id="rules.security.sqli-fp"
        )
        f_accept = Finding(
            id="f-acc", source=FindingSource.semgrep, severity=Severity.high,
            title="Path TP", description="desc", file="app.py",
            line_start=43, line_end=44, confidence=0.85, rule_id="rules.security.path"
        )

        # 3 rejections on f_reject (reward -1.0)
        for _ in range(3):
            a = p.get_priority_adjustment(f_reject)
            p.update(f_reject, reward=-1.0, action=a)

        # 2 acceptances on f_accept (reward +1.0)
        for _ in range(2):
            a = p.get_priority_adjustment(f_accept)
            p.update(f_accept, reward=1.0, action=a)

        # Switch to eval mode
        p.eval_mode = True

        # In eval mode, action 0.60 has the highest predicted Q for both findings
        pred_rej = {a: p.predict_reward(f_reject, a) for a in DISCRETE_ACTIONS}
        pred_acc = {a: p.predict_reward(f_accept, a) for a in DISCRETE_ACTIONS}

        # Action 0.6 has positive predicted reward; actions 1.0 and 1.4 have negative
        self.assertGreater(pred_rej[0.6], pred_rej[1.0])
        self.assertGreater(pred_acc[0.6], pred_acc[1.0])

        self.assertEqual(p.get_priority_adjustment(f_reject), 0.6)
        self.assertEqual(p.get_priority_adjustment(f_accept), 0.6)


if __name__ == "__main__":
    unittest.main()
