"""
Run the multimodal comparative experiment:
1. Load or generate ambiguous commands dataset
2. Deterministically split into train/test (80/20 stratified)
3. Train Baseline A (Transcript-only)
4. Train Approach B (Transcript + Code Context Fusion)
5. Evaluate both models across accuracy, precision, recall, and F1 (macro/weighted)
6. Output side-by-side comparison table
7. Save full comparison to results_comparison.json
"""

import json
import os
from typing import Any, Dict, List, Tuple
from sklearn.model_selection import train_test_split

from audio_code_fusion import (
    AudioCodeFusionClassifier,
    TranscriptOnlyClassifier,
    evaluate_model,
)
from generate_dataset import generate_multimodal_dataset, save_dataset


def run_comparative_experiment(
    samples_per_class: int = 100,
    seed: int = 42,
    test_size: float = 0.20,
) -> Dict[str, Any]:
    exp_dir = os.path.dirname(os.path.abspath(__file__))
    dataset_path = os.path.join(exp_dir, "dataset.json")
    results_path = os.path.join(exp_dir, "results_comparison.json")

    # Generate or load dataset
    if not os.path.exists(dataset_path):
        print("Dataset not found. Generating...")
        dataset = generate_multimodal_dataset(samples_per_class=samples_per_class, seed=seed)
        save_dataset(dataset, dataset_path)
    else:
        with open(dataset_path, "r", encoding="utf-8") as f:
            dataset = json.load(f)

    # Prepare features and labels
    X_audio = [d["audio"] for d in dataset]
    X_code = [d["code"] for d in dataset]
    y = [d["label"] for d in dataset]

    # Deterministic stratified 80/20 train/test split
    indices = list(range(len(dataset)))
    train_idx, test_idx = train_test_split(
        indices,
        test_size=test_size,
        random_state=seed,
        stratify=y,
    )

    X_train_audio = [X_audio[i] for i in train_idx]
    X_train_code = [X_code[i] for i in train_idx]
    y_train = [y[i] for i in train_idx]

    X_test_audio = [X_audio[i] for i in test_idx]
    X_test_code = [X_code[i] for i in test_idx]
    y_test = [y[i] for i in test_idx]

    unique_labels = sorted(list(set(y)))

    print(f"Total samples: {len(dataset)} | Train: {len(train_idx)} | Test: {len(test_idx)}")
    print(f"Classes: {unique_labels}")

    # Train Baseline A (Transcript-only)
    print("\nTraining Baseline A (Transcript-only)...")
    model_baseline = TranscriptOnlyClassifier(random_state=seed)
    model_baseline.fit(X_train_audio, y_train)
    y_pred_baseline = model_baseline.predict(X_test_audio)
    metrics_baseline = evaluate_model(y_test, y_pred_baseline.tolist(), labels=unique_labels)

    # Train Approach B (Transcript + Code Fusion)
    print("Training Approach B (Transcript + Code Context Fusion)...")
    model_fusion = AudioCodeFusionClassifier(random_state=seed)
    model_fusion.fit(X_train_audio, X_train_code, y_train)
    y_pred_fusion = model_fusion.predict(X_test_audio, X_test_code)
    metrics_fusion = evaluate_model(y_test, y_pred_fusion.tolist(), labels=unique_labels)

    comparison = {
        "metadata": {
            "total_samples": len(dataset),
            "train_samples": len(train_idx),
            "test_samples": len(test_idx),
            "classes": unique_labels,
            "seed": seed,
            "note": "Transcript text features vs transcript + code context feature fusion (not acoustic waveforms)",
        },
        "baseline_transcript_only": metrics_baseline,
        "approach_transcript_code_fusion": metrics_fusion,
    }

    # Print comparison table
    print("\n" + "=" * 70)
    print("COMPARATIVE EVALUATION: TRANSCRIPT-ONLY VS. TRANSCRIPT + CODE CONTEXT")
    print("=" * 70)
    print(f"{'Metric':<25} | {'Baseline A (Transcript)':<20} | {'Approach B (Fusion)':<20}")
    print("-" * 70)
    print(f"{'Accuracy':<25} | {metrics_baseline['accuracy']:<20.4f} | {metrics_fusion['accuracy']:<20.4f}")
    print(f"{'F1 (Macro)':<25} | {metrics_baseline['f1_macro']:<20.4f} | {metrics_fusion['f1_macro']:<20.4f}")
    print(f"{'Precision (Macro)':<25} | {metrics_baseline['precision_macro']:<20.4f} | {metrics_fusion['precision_macro']:<20.4f}")
    print(f"{'Recall (Macro)':<25} | {metrics_baseline['recall_macro']:<20.4f} | {metrics_fusion['recall_macro']:<20.4f}")
    print(f"{'F1 (Weighted)':<25} | {metrics_baseline['f1_weighted']:<20.4f} | {metrics_fusion['f1_weighted']:<20.4f}")
    print("-" * 70)

    print("\nPer-Class F1 Breakdown:")
    print(f"{'Class':<20} | {'Baseline A':<15} | {'Approach B':<15}")
    print("-" * 55)
    for cls in unique_labels:
        f1_a = metrics_baseline["per_class"][cls]["f1"]
        f1_b = metrics_fusion["per_class"][cls]["f1"]
        print(f"{cls:<20} | {f1_a:<15.4f} | {f1_b:<15.4f}")
    print("=" * 70)

    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(comparison, f, indent=2)

    # Also write results.json for backwards compatibility
    with open(os.path.join(exp_dir, "results.json"), "w", encoding="utf-8") as f:
        json.dump(metrics_fusion, f, indent=2)

    print(f"\nSaved detailed comparison to {results_path}")
    return comparison


if __name__ == "__main__":
    run_comparative_experiment()
