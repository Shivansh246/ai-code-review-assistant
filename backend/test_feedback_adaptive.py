"""
test_feedback_adaptive.py
Unit tests for Phase 6A Transparent Adaptive Feedback Baseline:
1. get_feedback_stats aggregation
2. Adaptive scoring & multiplier calculation
3. Integration with /review endpoints
4. Fallback handling on DB errors
"""

import asyncio
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


from models import FeedbackEvent, FeedbackStatus, Finding, FindingSource, Severity
import feedback
import scoring
import main


class TestFeedbackAggregation(unittest.TestCase):
    """Tests for feedback database aggregation functions."""

    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        feedback.DB_PATH = self.tmp_db.name

    def tearDown(self):
        if os.path.exists(self.tmp_db.name):
            os.remove(self.tmp_db.name)

    def test_empty_database_stats(self):
        async def run_test():
            await feedback.init_db()
            stats = await feedback.get_feedback_stats()
            self.assertEqual(stats["rule_stats"], {})
            self.assertEqual(stats["source_stats"], {})
        asyncio.run(run_test())

    def test_feedback_aggregation_counts(self):
        async def run_test():
            await feedback.init_db()
            f1 = Finding(
                id="f1",
                source=FindingSource.semgrep,
                severity=Severity.high,
                title="SQLi",
                description="SQL injection",
                file="app.py",
                line_start=1,
                line_end=1,
                confidence=0.9,
                rule_id="rule-sqli"
            )
            f2 = Finding(
                id="f2",
                source=FindingSource.semgrep,
                severity=Severity.medium,
                title="MD5",
                description="MD5 hash",
                file="app.py",
                line_start=5,
                line_end=5,
                confidence=0.8,
                rule_id="rule-md5"
            )
            await feedback.cache_finding(f1)
            await feedback.cache_finding(f2)

            # Store feedback
            e1 = FeedbackEvent(finding_id="f1", status=FeedbackStatus.rejected, timestamp="2026-10-07T00:00:00Z")
            e2 = FeedbackEvent(finding_id="f1", status=FeedbackStatus.rejected, timestamp="2026-10-07T00:01:00Z")
            e3 = FeedbackEvent(finding_id="f2", status=FeedbackStatus.accepted, timestamp="2026-10-07T00:02:00Z")
            
            await feedback.store_feedback(e1)
            await feedback.store_feedback(e2)
            await feedback.store_feedback(e3)

            stats = await feedback.get_feedback_stats()
            self.assertEqual(stats["rule_stats"]["rule-sqli"]["rejected"], 2)
            self.assertEqual(stats["rule_stats"]["rule-sqli"]["accepted"], 0)
            self.assertEqual(stats["rule_stats"]["rule-md5"]["accepted"], 1)
            self.assertEqual(stats["source_stats"]["semgrep"]["rejected"], 2)
            self.assertEqual(stats["source_stats"]["semgrep"]["accepted"], 1)

        asyncio.run(run_test())

    def test_missing_cache_or_rule_id_handling(self):
        async def run_test():
            await feedback.init_db()
            e1 = FeedbackEvent(finding_id="non-cached-id", status=FeedbackStatus.rejected, timestamp="2026-10-07T00:00:00Z")
            await feedback.store_feedback(e1)

            stats = await feedback.get_feedback_stats()
            self.assertEqual(stats["rule_stats"], {})
            self.assertEqual(stats["source_stats"], {})
        asyncio.run(run_test())


class TestAdaptiveScoring(unittest.TestCase):
    """Tests for adaptive scoring formulas and multiplier bounds."""

    def test_zero_feedback_unchanged(self):
        f = Finding(
            source=FindingSource.semgrep,
            severity=Severity.high,
            title="Finding",
            description="Desc",
            file="app.py",
            line_start=1,
            line_end=1,
            confidence=0.9,
            rule_id="rule-test"
        )
        base_score = scoring.score_finding(f)
        mult = scoring.compute_adaptive_multiplier(f, {})
        adaptive_score = scoring.score_finding_adaptive(f, {})
        
        self.assertEqual(mult, 1.0)
        self.assertAlmostEqual(adaptive_score, base_score, places=2)


    def test_rejection_reduces_score(self):
        f = Finding(
            source=FindingSource.semgrep,
            severity=Severity.high,
            title="Finding",
            description="Desc",
            file="app.py",
            line_start=1,
            line_end=1,
            confidence=0.9,
            rule_id="rule-sqli"
        )
        stats = {
            "rule_stats": {"rule-sqli": {"accepted": 0, "rejected": 2}},
            "source_stats": {}
        }
        base_score = scoring.score_finding(f)
        adaptive_score = scoring.score_finding_adaptive(f, stats)

        self.assertLess(adaptive_score, base_score)
        self.assertEqual(adaptive_score, round(base_score * 0.5, 2))

    def test_acceptance_increases_score(self):
        f = Finding(
            source=FindingSource.llm,
            severity=Severity.medium,
            title="Finding",
            description="Desc",
            file="app.py",
            line_start=1,
            line_end=1,
            confidence=0.8,
            rule_id="rule-acc"
        )
        stats = {
            "rule_stats": {"rule-acc": {"accepted": 2, "rejected": 0}},
            "source_stats": {}
        }
        base_score = scoring.score_finding(f)
        adaptive_score = scoring.score_finding_adaptive(f, stats)

        self.assertGreater(adaptive_score, base_score)
        self.assertEqual(adaptive_score, round(base_score * 1.1, 2))

    def test_multiplier_bounds_clamping(self):
        f = Finding(
            source=FindingSource.semgrep,
            severity=Severity.high,
            title="Finding",
            description="Desc",
            file="app.py",
            line_start=1,
            line_end=1,
            confidence=0.9,
            rule_id="rule-heavy-reject"
        )
        # 100 rejections -> multiplier should clamp at 0.1
        stats_reject = {
            "rule_stats": {"rule-heavy-reject": {"accepted": 0, "rejected": 100}},
            "source_stats": {}
        }
        mult_reject = scoring.compute_adaptive_multiplier(f, stats_reject)
        self.assertEqual(mult_reject, 0.1)

        # 100 acceptances -> multiplier should clamp at 1.2
        stats_accept = {
            "rule_stats": {"rule-heavy-reject": {"accepted": 100, "rejected": 0}},
            "source_stats": {}
        }
        mult_accept = scoring.compute_adaptive_multiplier(f, stats_accept)
        self.assertEqual(mult_accept, 1.2)

    def test_unrelated_rule_or_source_unchanged(self):
        f = Finding(
            source=FindingSource.osv,
            severity=Severity.high,
            title="Finding",
            description="Desc",
            file="requirements.txt",
            line_start=1,
            line_end=1,
            confidence=1.0,
            rule_id="rule-other"
        )
        stats = {
            "rule_stats": {"rule-sqli": {"accepted": 0, "rejected": 5}},
            "source_stats": {"semgrep": {"accepted": 0, "rejected": 5}}
        }
        mult = scoring.compute_adaptive_multiplier(f, stats)
        self.assertEqual(mult, 1.0)


class TestReviewIntegrationFallback(unittest.TestCase):
    """Tests for main.py integration and fallback behavior."""

    def test_review_fallback_on_db_error(self):
        async def run_test():
            f1 = Finding(
                source=FindingSource.semgrep,
                severity=Severity.high,
                title="SQLi",
                description="Desc",
                file="app.py",
                line_start=1,
                line_end=1,
                confidence=0.9,
                rule_id="rule-sqli"
            )
            with patch("scanner_semgrep.scan_file", AsyncMock(return_value=[f1])):
                with patch("scanner_llm.scan_file", AsyncMock(return_value=[])):
                    with patch("scanner_osv.scan_file", AsyncMock(return_value=[])):
                        with patch("feedback.get_feedback_stats", side_effect=Exception("Database locked")):
                            req = main.ReviewRequest(file_path="app.py", content="content")
                            resp = await main.review_file(req)
                            self.assertEqual(len(resp.findings), 1)
                            self.assertEqual(resp.findings[0].title, "SQLi")

        asyncio.run(run_test())


class TestEvaluationRunner(unittest.TestCase):
    """Tests for evaluation/eval_feedback.py functionality."""

    def test_eval_feedback_runner(self):
        sys_path_orig = list(sys.path)
        eval_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "evaluation"))
        if eval_path not in sys.path:
            sys.path.insert(0, eval_path)

        import eval_feedback
        results = eval_feedback.run_evaluation()

        self.assertIn("baseline_metrics", results)
        self.assertIn("adaptive_metrics", results)
        self.assertIn("f1_delta", results)
        self.assertIn("f1_status", results)
        self.assertIn("score_demotion_rate", results)
        self.assertIn("rank_demotion_rate", results)
        self.assertIn("top3_fp_reduction", results)

        # 1. At least one rejected FP starts in top 3
        self.assertEqual(results["top3_fp_before"], 1)
        # 2. That finding moves below top-3 cutoff (FP in top-3 drops to 0)
        self.assertEqual(results["top3_fp_after"], 0)
        self.assertEqual(results["top3_fp_reduction"], 1)

        # 3. Score & rank demotion rates computed correctly
        self.assertEqual(results["score_demotion_rate"], 100.0)
        self.assertEqual(results["rank_demotion_rate"], 100.0)

        # 4. Precision, Recall, F1, and F1 delta computed correctly
        self.assertGreater(results["baseline_metrics"]["f1"], 0.0)
        self.assertGreater(results["adaptive_metrics"]["f1"], results["baseline_metrics"]["f1"])
        self.assertGreater(results["f1_delta"], 0.0)
        self.assertEqual(results["f1_status"], "PASS")

        # 5. Deterministic repeated execution
        repeat_results = eval_feedback.run_evaluation()
        self.assertEqual(results, repeat_results)

        sys.path = sys_path_orig


if __name__ == "__main__":
    unittest.main()


