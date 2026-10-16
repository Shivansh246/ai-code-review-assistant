"""
Multimodal Experiment: Audio and Code Fusion Comparison.

This module provides two comparative models for intent classification:
1. Baseline A (TranscriptOnlyClassifier): Uses only the voice transcript text.
2. Approach B (AudioCodeFusionClassifier): Fuses voice transcript features with
   code context features to resolve ambiguous commands.

Note on modality:
This is a transcript-only vs. transcript-plus-code-context textual feature fusion
experiment, rather than raw acoustic waveform embeddings.
"""

from typing import Any, Dict, List
import numpy as np
from sklearn.feature_extraction.text import HashingVectorizer
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score
from sklearn.neural_network import MLPClassifier


# Stateless feature extraction
audio_vectorizer = HashingVectorizer(n_features=128, alternate_sign=False)
code_vectorizer = HashingVectorizer(n_features=512, alternate_sign=False)


def extract_audio_features(transcript: str) -> np.ndarray:
    """Extract text features from voice transcript."""
    return audio_vectorizer.transform([transcript]).toarray()[0]


def extract_code_features(code: str) -> np.ndarray:
    """Extract code token features from code context."""
    return code_vectorizer.transform([code]).toarray()[0]


def evaluate_model(y_true: List[str], y_pred: List[str], labels: List[str] = None) -> Dict[str, Any]:
    """
    Compute multi-class evaluation metrics:
    - Accuracy
    - Macro and weighted Precision, Recall, F1
    - Per-class breakdown
    - Confusion matrix
    """
    if labels is None:
        labels = sorted(list(set(y_true) | set(y_pred)))

    prec_macro = float(precision_score(y_true, y_pred, average="macro", zero_division=0))
    rec_macro = float(recall_score(y_true, y_pred, average="macro", zero_division=0))
    f1_macro = float(f1_score(y_true, y_pred, average="macro", zero_division=0))

    prec_weighted = float(precision_score(y_true, y_pred, average="weighted", zero_division=0))
    rec_weighted = float(recall_score(y_true, y_pred, average="weighted", zero_division=0))
    f1_weighted = float(f1_score(y_true, y_pred, average="weighted", zero_division=0))

    prec_per_class = precision_score(y_true, y_pred, labels=labels, average=None, zero_division=0).tolist()
    rec_per_class = recall_score(y_true, y_pred, labels=labels, average=None, zero_division=0).tolist()
    f1_per_class = f1_score(y_true, y_pred, labels=labels, average=None, zero_division=0).tolist()

    per_class = {
        label: {
            "precision": float(prec_per_class[i]),
            "recall": float(rec_per_class[i]),
            "f1": float(f1_per_class[i]),
        }
        for i, label in enumerate(labels)
    }

    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision_macro": prec_macro,
        "recall_macro": rec_macro,
        "f1_macro": f1_macro,
        "precision_weighted": prec_weighted,
        "recall_weighted": rec_weighted,
        "f1_weighted": f1_weighted,
        "labels": labels,
        "per_class": per_class,
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=labels).tolist(),
    }


class TranscriptOnlyClassifier:
    """
    Baseline A: Classifier using only voice transcript features.
    """
    def __init__(self, random_state: int = 42):
        self.classifier = MLPClassifier(
            hidden_layer_sizes=(64, 32),
            max_iter=500,
            random_state=random_state,
        )

    def fit(self, X_audio: List[str], y: List[str]) -> "TranscriptOnlyClassifier":
        features = np.array([extract_audio_features(t) for t in X_audio])
        self.classifier.fit(features, y)
        return self

    def predict(self, X_audio: List[str]) -> np.ndarray:
        features = np.array([extract_audio_features(t) for t in X_audio])
        return self.classifier.predict(features)


class AudioCodeFusionClassifier:
    """
    Approach B: Multimodal classifier concatenating audio transcript
    and code context features.
    """
    def __init__(self, random_state: int = 42):
        self.classifier = MLPClassifier(
            hidden_layer_sizes=(64, 32),
            max_iter=500,
            random_state=random_state,
        )

    def fit(self, X_audio: List[str], X_code: List[str], y: List[str]) -> "AudioCodeFusionClassifier":
        features = self._prepare_features(X_audio, X_code)
        self.classifier.fit(features, y)
        return self

    def predict(self, X_audio: List[str], X_code: List[str]) -> np.ndarray:
        features = self._prepare_features(X_audio, X_code)
        return self.classifier.predict(features)

    def _prepare_features(self, X_audio: List[str], X_code: List[str]) -> np.ndarray:
        audio_feats = np.array([extract_audio_features(t) for t in X_audio])
        code_feats = np.array([extract_code_features(c) for c in X_code])
        return np.hstack((audio_feats, code_feats))
