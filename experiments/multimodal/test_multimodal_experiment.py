"""
Unit tests for the comparative multimodal experiment:
- Dataset generation and schema validity
- Deterministic stratified splitting
- Zero train/test leakage
- Metric computation (accuracy, macro/weighted precision, recall, F1, per-class breakdown)
- Model execution and comparative evaluation
"""

import json
import os
import tempfile
import unittest
from sklearn.model_selection import train_test_split

from audio_code_fusion import (
    AudioCodeFusionClassifier,
    TranscriptOnlyClassifier,
    evaluate_model,
    extract_audio_features,
    extract_code_features,
)
from generate_dataset import (
    AMBIGUOUS_COMMAND_TEMPLATES,
    CODE_CONTEXT_TEMPLATES,
    generate_multimodal_dataset,
    save_dataset,
)
from run_experiment import run_comparative_experiment


class TestMultimodalDatasetGeneration(unittest.TestCase):
    def test_dataset_generation_count_and_balance(self):
        dataset = generate_multimodal_dataset(samples_per_class=20, seed=123)
        self.assertEqual(len(dataset), 60)

        class_counts = {}
        for sample in dataset:
            self.assertIn("audio", sample)
            self.assertIn("code", sample)
            self.assertIn("label", sample)
            self.assertTrue(sample["audio"].strip())
            self.assertTrue(sample["code"].strip())
            lbl = sample["label"]
            class_counts[lbl] = class_counts.get(lbl, 0) + 1

        self.assertEqual(set(class_counts.keys()), {"review", "explain", "generate_fix"})
        for count in class_counts.values():
            self.assertEqual(count, 20)

    def test_deterministic_generation(self):
        d1 = generate_multimodal_dataset(samples_per_class=10, seed=99)
        d2 = generate_multimodal_dataset(samples_per_class=10, seed=99)
        self.assertEqual(d1, d2)

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "test_dataset.json")
            data = generate_multimodal_dataset(samples_per_class=5, seed=42)
            save_dataset(data, out_path)
            self.assertTrue(os.path.exists(out_path))

            with open(out_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self.assertEqual(data, loaded)


class TestFeatureExtractionAndEvaluationMetrics(unittest.TestCase):
    def test_feature_extraction_shapes(self):
        audio_feat = extract_audio_features("check this file")
        code_feat = extract_code_features("def foo(): pass")
        self.assertEqual(audio_feat.shape, (128,))
        self.assertEqual(code_feat.shape, (512,))

    def test_evaluate_model_metrics(self):
        y_true = ["review", "explain", "generate_fix", "review"]
        y_pred = ["review", "explain", "review", "review"]  # 3 correct, 1 error

        metrics = evaluate_model(y_true, y_pred, labels=["explain", "generate_fix", "review"])
        self.assertEqual(metrics["accuracy"], 0.75)
        self.assertIn("precision_macro", metrics)
        self.assertIn("recall_macro", metrics)
        self.assertIn("f1_macro", metrics)
        self.assertIn("precision_weighted", metrics)
        self.assertIn("recall_weighted", metrics)
        self.assertIn("f1_weighted", metrics)
        self.assertIn("per_class", metrics)
        self.assertIn("confusion_matrix", metrics)

        # Per-class metrics
        self.assertEqual(metrics["per_class"]["explain"]["precision"], 1.0)
        self.assertEqual(metrics["per_class"]["explain"]["recall"], 1.0)
        self.assertEqual(metrics["per_class"]["explain"]["f1"], 1.0)


class TestComparativeModelsAndLeakage(unittest.TestCase):
    def test_deterministic_split_and_no_leakage(self):
        dataset = generate_multimodal_dataset(samples_per_class=30, seed=42)
        y = [d["label"] for d in dataset]
        indices = list(range(len(dataset)))

        train_idx, test_idx = train_test_split(
            indices,
            test_size=0.20,
            random_state=42,
            stratify=y,
        )

        self.assertEqual(len(train_idx), 72)
        self.assertEqual(len(test_idx), 18)
        self.assertEqual(len(set(train_idx) & set(test_idx)), 0)

    def test_classifiers_fit_and_predict(self):
        dataset = generate_multimodal_dataset(samples_per_class=20, seed=42)
        X_audio = [d["audio"] for d in dataset]
        X_code = [d["code"] for d in dataset]
        y = [d["label"] for d in dataset]

        # Baseline A
        clf_a = TranscriptOnlyClassifier(random_state=42)
        clf_a.fit(X_audio[:40], y[:40])
        preds_a = clf_a.predict(X_audio[40:])
        self.assertEqual(len(preds_a), 20)

        # Approach B
        clf_b = AudioCodeFusionClassifier(random_state=42)
        clf_b.fit(X_audio[:40], X_code[:40], y[:40])
        preds_b = clf_b.predict(X_audio[40:], X_code[40:])
        self.assertEqual(len(preds_b), 20)

    def test_run_comparative_experiment_execution(self):
        results = run_comparative_experiment(samples_per_class=10, seed=42, test_size=0.2)
        self.assertIn("baseline_transcript_only", results)
        self.assertIn("approach_transcript_code_fusion", results)
        self.assertIn("accuracy", results["baseline_transcript_only"])
        self.assertIn("accuracy", results["approach_transcript_code_fusion"])


if __name__ == "__main__":
    unittest.main()
