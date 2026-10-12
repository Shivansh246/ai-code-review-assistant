"""
test_rl_experiment.py
Dedicated unit-test suite for discrete-action contextual bandit learning in backend/rl_experiment.py.
"""

import json
import math
import os
import tempfile
import unittest

from models import Finding, FindingSource, Severity
from rl_experiment import (
    AdaptivePrioritizer,
    DISCRETE_ACTIONS,
    NUM_RULE_BUCKETS,
    _get_rule_bucket,
    feature_vector,
)


class TestRLFeatureExtraction(unittest.TestCase):
    """Tests for feature vector construction and enum handling."""

    def test_all_severity_enums(self):
        severities = [
            (Severity.critical, 4.0),
            (Severity.high, 3.0),
            (Severity.medium, 2.0),
            (Severity.low, 1.0),
            (Severity.info, 0.0),
        ]
        for sev, expected_val in severities:
            f = Finding(
                source=FindingSource.semgrep,
                severity=sev,
                title="Test",
                description="Desc",
                file="app.py",
                line_start=1,
                line_end=1,
                confidence=0.9
            )
            feats = feature_vector(f)
            self.assertEqual(feats["severity"], expected_val)

    def test_all_source_enums(self):
        sources = [
            (FindingSource.semgrep, "src_semgrep"),
            (FindingSource.llm, "src_llm"),
            (FindingSource.osv, "src_osv"),
            (FindingSource.fused, "src_fused"),
        ]
        for src, key in sources:
            f = Finding(
                source=src,
                severity=Severity.high,
                title="Test",
                description="Desc",
                file="app.py",
                line_start=1,
                line_end=1,
                confidence=0.8
            )
            feats = feature_vector(f)
            self.assertEqual(feats[key], 1.0)
            other_keys = {"src_semgrep", "src_llm", "src_osv", "src_fused"} - {key}
            for ok in other_keys:
                self.assertEqual(feats[ok], 0.0)

    def test_missing_optional_identifiers(self):
        f = Finding(
            source=FindingSource.semgrep,
            severity=Severity.high,
            title="Test",
            description="Desc",
            file="app.py",
            line_start=1,
            line_end=1,
            confidence=0.5,
            rule_id=None,
            cve_id=None
        )
        feats = feature_vector(f)
        self.assertEqual(feats["has_rule_id"], 0.0)
        self.assertEqual(feats["has_cve_id"], 0.0)
        for i in range(NUM_RULE_BUCKETS):
            self.assertEqual(feats[f"rule_bucket_{i}"], 0.0)

    def test_rule_id_hashing_determinism(self):
        f1 = Finding(
            source=FindingSource.semgrep,
            severity=Severity.high,
            title="SQLi",
            description="Desc",
            file="app.py",
            line_start=1,
            line_end=1,
            confidence=0.9,
            rule_id="rules.python.security.sqli"
        )
        f2 = Finding(
            source=FindingSource.semgrep,
            severity=Severity.high,
            title="SQLi",
            description="Desc",
            file="app.py",
            line_start=1,
            line_end=1,
            confidence=0.9,
            rule_id="rules.python.security.sqli"
        )
        feats1 = feature_vector(f1)
        feats2 = feature_vector(f2)
        self.assertEqual(feats1, feats2)

        bucket_idx = _get_rule_bucket("rules.python.security.sqli")
        self.assertEqual(feats1[f"rule_bucket_{bucket_idx}"], 1.0)


class TestColdStartAndSafetyPolicy(unittest.TestCase):
    """Tests for cold-start safety, neutral multiplier selection, and transition to learned policy."""

    def test_cold_start_empty_history_eval_mode(self):
        p = AdaptivePrioritizer(eval_mode=True)
        f = Finding(
            id="f-cold-1",
            source=FindingSource.semgrep,
            severity=Severity.critical,
            title="SQLi",
            description="Desc",
            file="app.py",
            line_start=1,
            line_end=1,
            confidence=0.9
        )
        self.assertTrue(p.is_cold_start)
        self.assertEqual(p.update_count, 0)
        # In cold start and eval mode, must return neutral multiplier 1.0
        selected = p.get_priority_adjustment(f)
        self.assertEqual(selected, 1.0)

    def test_cold_start_zero_weights_no_arbitrary_ordering(self):
        # Even if update_count were set, all-zero weights must still trigger cold-start policy
        p = AdaptivePrioritizer(eval_mode=True)
        f = Finding(
            id="f-cold-zero",
            source=FindingSource.semgrep,
            severity=Severity.high,
            title="XSS",
            description="Desc",
            file="app.py",
            line_start=1,
            line_end=1,
            confidence=0.8
        )
        self.assertTrue(p._are_all_weights_zero())
        # Confirm it does NOT select 0.2 (which is actions[0])
        selected = p.get_priority_adjustment(f)
        self.assertEqual(selected, 1.0)

    def test_cold_start_missing_persistence_file(self):
        p = AdaptivePrioritizer(eval_mode=True)
        p.load("/tmp/non_existent_rl_weights_file.json")
        f = Finding(
            id="f-cold-missing",
            source=FindingSource.semgrep,
            severity=Severity.medium,
            title="Info Leak",
            description="Desc",
            file="app.py",
            line_start=1,
            line_end=1,
            confidence=0.7
        )
        self.assertTrue(p.is_cold_start)
        self.assertEqual(p.get_priority_adjustment(f), 1.0)

    def test_cold_start_legacy_or_empty_persistence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_path = os.path.join(temp_dir, "legacy_weights.json")
            # Legacy file without update_count or action_weights
            with open(legacy_path, "w") as f:
                json.dump({"epsilon": 0.05, "learning_rate": 0.01}, f)

            p = AdaptivePrioritizer(eval_mode=True)
            p.load(legacy_path)
            self.assertTrue(p.is_cold_start)
            self.assertEqual(p.update_count, 0)

            finding = Finding(
                id="f-cold-legacy",
                source=FindingSource.semgrep,
                severity=Severity.high,
                title="Command Injection",
                description="Desc",
                file="app.py",
                line_start=1,
                line_end=1,
                confidence=0.95
            )
            self.assertEqual(p.get_priority_adjustment(finding), 1.0)

    def test_transition_from_cold_start_to_learned_policy(self):
        evidence_threshold = 3
        p = AdaptivePrioritizer(learning_rate=0.5, eval_mode=True, evidence_threshold=evidence_threshold)
        f = Finding(
            id="f-trans",
            source=FindingSource.semgrep,
            severity=Severity.critical,
            title="Remote Code Execution",
            description="Desc",
            file="app.py",
            line_start=1,
            line_end=1,
            confidence=1.0,
            rule_id="rce-rule"
        )

        # Before reaching threshold, cold start is active -> neutral 1.0
        self.assertTrue(p.is_cold_start)
        self.assertEqual(p.get_priority_adjustment(f), 1.0)

        # Apply update 1
        p.update(f, reward=1.0, action=1.8)
        self.assertEqual(p.update_count, 1)
        self.assertTrue(p.is_cold_start)
        self.assertEqual(p.get_priority_adjustment(f), 1.0)

        # Apply update 2
        p.update(f, reward=1.0, action=1.8)
        self.assertEqual(p.update_count, 2)
        self.assertTrue(p.is_cold_start)
        self.assertEqual(p.get_priority_adjustment(f), 1.0)

        # Apply update 3 -> reaches evidence threshold!
        p.update(f, reward=1.0, action=1.8)
        self.assertEqual(p.update_count, 3)
        self.assertFalse(p.is_cold_start)

        # Now out of cold start: learned policy takes over and selects 1.8!
        selected_learned = p.get_priority_adjustment(f)
        self.assertEqual(selected_learned, 1.8)


class TestActionAssociationAndContextualLearning(unittest.TestCase):
    """Tests for action recording and action-conditioned contextual learning."""

    def test_action_association_recording(self):
        p = AdaptivePrioritizer(eval_mode=True)
        f = Finding(
            id="finding-assoc-123",
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
        selected_action = p.get_priority_adjustment(f)
        self.assertIn(selected_action, DISCRETE_ACTIONS)
        self.assertEqual(p.get_recorded_action("finding-assoc-123"), selected_action)

    def test_repeated_reviews_same_finding_fifo_attribution(self):
        p = AdaptivePrioritizer(learning_rate=0.2, eval_mode=True)
        f = Finding(
            id="finding-repeated-1",
            source=FindingSource.semgrep,
            severity=Severity.high,
            title="SSRF",
            description="Desc",
            file="app.py",
            line_start=1,
            line_end=1,
            confidence=0.9,
            rule_id="rule-ssrf"
        )

        # Review 1 records action 1.4
        p.record_action("finding-repeated-1", 1.4)
        # Review 2 of same finding records action 0.6
        p.record_action("finding-repeated-1", 0.6)

        w_14_before = dict(p.action_weights["1.4"])
        w_06_before = dict(p.action_weights["0.6"])

        # First feedback arrives for Review 1 (FIFO pop)
        p.update(f, reward=1.0)
        # Should have updated action 1.4, leaving 0.6 untouched
        w_14_after = p.action_weights["1.4"]
        w_06_after = p.action_weights["0.6"]
        self.assertNotEqual(w_14_before["severity"], w_14_after["severity"])
        self.assertEqual(w_06_before["severity"], w_06_after["severity"])

        # Second feedback arrives for Review 2 (FIFO pop)
        p.update(f, reward=-1.0)
        w_06_after2 = p.action_weights["0.6"]
        self.assertNotEqual(w_06_before["severity"], w_06_after2["severity"])

    def test_missing_action_association_skips_update_safely(self):
        p = AdaptivePrioritizer(eval_mode=True)
        f = Finding(
            id="finding-unrecorded",
            source=FindingSource.semgrep,
            severity=Severity.high,
            title="SQLi",
            description="Desc",
            file="app.py",
            line_start=1,
            line_end=1,
            confidence=0.9
        )
        weights_10_before = dict(p.action_weights["1.0"])
        # Update without explicit action and without prior record
        p.update(f, reward=1.0)

        # Weights of action 1.0 must NOT be modified
        self.assertEqual(p.action_weights["1.0"], weights_10_before)
        # Update count must NOT increment
        self.assertEqual(p.update_count, 0)

    def test_action_conditioned_sgd_update(self):
        p = AdaptivePrioritizer(learning_rate=0.2, eval_mode=True)
        f = Finding(
            id="finding-test-1",
            source=FindingSource.semgrep,
            severity=Severity.high,
            title="SQLi",
            description="Desc",
            file="app.py",
            line_start=1,
            line_end=1,
            confidence=1.0,
            rule_id="rule-sqli"
        )

        # Record action 0.2 (low multiplier) and apply negative reward (-1.0)
        p.record_action("finding-test-1", 0.2)
        w_02_before = dict(p.action_weights["0.2"])
        w_18_before = dict(p.action_weights["1.8"])

        p.update(f, reward=-1.0)

        # Action 0.2 weights should change
        w_02_after = p.action_weights["0.2"]
        w_18_after = p.action_weights["1.8"]

        self.assertNotEqual(w_02_before["severity"], w_02_after["severity"])
        # Action 1.8 weights must remain UNCHANGED (Action isolation property)
        self.assertEqual(w_18_before["severity"], w_18_after["severity"])

    def test_positive_feedback_promotes_greedy_action(self):
        p = AdaptivePrioritizer(learning_rate=0.5, eval_mode=True, evidence_threshold=3)
        f = Finding(
            id="finding-promote-1",
            source=FindingSource.semgrep,
            severity=Severity.critical,
            title="Command Injection",
            description="Desc",
            file="app.py",
            line_start=1,
            line_end=1,
            confidence=1.0,
            rule_id="rule-cmd-injection"
        )

        # Apply positive reward (+1.0) repeatedly to action 1.8
        for _ in range(5):
            p.update(f, reward=1.0, action=1.8)

        # Evaluation choice should select action 1.8
        selected = p.get_priority_adjustment(f)
        self.assertEqual(selected, 1.8)

    def test_non_finite_reward_safety(self):
        p = AdaptivePrioritizer(eval_mode=True)
        f = Finding(
            id="f-safety",
            source=FindingSource.semgrep,
            severity=Severity.high,
            title="Test",
            description="Desc",
            file="app.py",
            line_start=1,
            line_end=1,
            confidence=0.9
        )
        weights_before = dict(p.action_weights["1.0"])
        p.update(f, float("nan"), action=1.0)
        p.update(f, float("inf"), action=1.0)
        self.assertEqual(p.action_weights["1.0"], weights_before)


class TestReproducibilityAndPersistence(unittest.TestCase):
    """Tests for random seeding, eval mode, and v1.3 persistence."""

    def test_seeded_reproducibility(self):
        # Use evidence_threshold=1 and train 1 update so epsilon exploration is active
        p1 = AdaptivePrioritizer(epsilon=0.8, seed=999, evidence_threshold=1)
        p2 = AdaptivePrioritizer(epsilon=0.8, seed=999, evidence_threshold=1)

        f = Finding(
            source=FindingSource.semgrep,
            severity=Severity.high,
            title="Test",
            description="Desc",
            file="app.py",
            line_start=1,
            line_end=1,
            confidence=0.9
        )
        p1.update(f, reward=1.0, action=1.8)
        p2.update(f, reward=1.0, action=1.8)

        actions1 = [p1.get_priority_adjustment(f) for _ in range(10)]
        actions2 = [p2.get_priority_adjustment(f) for _ in range(10)]
        self.assertEqual(actions1, actions2)

    def test_save_load_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            weights_path = os.path.join(temp_dir, "weights_v13.json")
            p1 = AdaptivePrioritizer(epsilon=0.15, learning_rate=0.02, seed=42, evidence_threshold=7)
            p1.action_weights["1.8"]["severity"] = 2.5
            p1.update_count = 12
            p1.save(weights_path)

            p2 = AdaptivePrioritizer()
            p2.load(weights_path)

            self.assertEqual(p2.epsilon, 0.15)
            self.assertEqual(p2.learning_rate, 0.02)
            self.assertEqual(p2.evidence_threshold, 7)
            self.assertEqual(p2.update_count, 12)
            self.assertEqual(p2.action_weights["1.8"]["severity"], 2.5)

    def test_corrupt_file_handling(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            weights_path = os.path.join(temp_dir, "corrupt.json")
            with open(weights_path, "w") as f:
                f.write("{invalid_json_content...")

            p = AdaptivePrioritizer()
            default_10 = dict(p.action_weights["1.0"])
            p.load(weights_path)
            self.assertEqual(p.action_weights["1.0"], default_10)


if __name__ == "__main__":
    unittest.main()
