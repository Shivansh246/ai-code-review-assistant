"""
Multimodal Experiment: Audio and Code Fusion Proof of Concept.

This module provides a basic fusion architecture to combine audio (voice command text proxy)
and code features to classify if a voice command correctly maps to a given code context.
"""

import numpy as np
from sklearn.feature_extraction.text import HashingVectorizer
from sklearn.neural_network import MLPClassifier
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, confusion_matrix
from typing import List, Dict, Any

# We use HashingVectorizer to allow stateless feature extraction for single strings
# without needing to fit a vocabulary first.
audio_vectorizer = HashingVectorizer(n_features=128, alternate_sign=False)
code_vectorizer = HashingVectorizer(n_features=512, alternate_sign=False)

def extract_audio_features(transcript: str) -> np.ndarray:
    """
    Extract features from voice transcript.
    Using HashingVectorizer as a proxy for audio/text embeddings.
    """
    return audio_vectorizer.transform([transcript]).toarray()[0]

def extract_code_features(code: str) -> np.ndarray:
    """
    Extract features from code context.
    Using HashingVectorizer for code tokens.
    """
    return code_vectorizer.transform([code]).toarray()[0]

def evaluate_model(y_true: List[int], y_pred: List[int]) -> Dict[str, Any]:
    """
    Compute accuracy, precision, recall, and F1 score.
    """
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "confusion_matrix": confusion_matrix(y_true, y_pred).tolist()
    }

class AudioCodeFusionClassifier:
    """
    A simple multimodal classifier that concatenates audio and code features,
    and feeds them through an MLP classifier.
    """
    def __init__(self):
        # 2-layer MLP as requested
        self.classifier = MLPClassifier(hidden_layer_sizes=(64, 32), max_iter=500, random_state=42)

    def fit(self, X_audio: List[str], X_code: List[str], y: List[int]):
        """
        Train the classifier on a dataset of audio transcripts and code contexts.
        """
        features = self._prepare_features(X_audio, X_code)
        self.classifier.fit(features, y)

    def predict(self, X_audio: List[str], X_code: List[str]) -> np.ndarray:
        """
        Predict whether the voice command maps correctly to the code.
        """
        features = self._prepare_features(X_audio, X_code)
        return self.classifier.predict(features)
        
    def _prepare_features(self, X_audio: List[str], X_code: List[str]) -> np.ndarray:
        """
        Extract and concatenate features.
        """
        audio_feats = np.array([extract_audio_features(t) for t in X_audio])
        code_feats = np.array([extract_code_features(c) for c in X_code])
        # Concatenate features
        return np.hstack((audio_feats, code_feats))
