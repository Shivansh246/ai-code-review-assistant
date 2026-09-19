"""
EXPERIMENTAL MODULE: rl_experiment
This module implements a simple contextual bandit for adaptive finding prioritization.
"""

import os
import json
import logging
import random
from typing import Dict
from models import Finding, FindingSource, Severity, FeedbackEvent, FeedbackStatus

logger = logging.getLogger(__name__)

def feature_vector(finding: Finding) -> Dict[str, float]:
    """
    Extract features from a finding.
    Features:
    - severity: critical=4, high=3, medium=2, low=1, info=0
    - source: one-hot (semgrep, llm, osv, fused)
    - confidence: float
    - has_rule_id: 0 or 1
    - has_cve_id: 0 or 1
    """
    severity_map = {
        Severity.CRITICAL: 4.0,
        Severity.HIGH: 3.0,
        Severity.MEDIUM: 2.0,
        Severity.LOW: 1.0,
        Severity.INFO: 0.0
    }
    
    # Extract one-hot encoded source
    src_semgrep = 1.0 if finding.source == FindingSource.SEMGREP else 0.0
    src_llm = 1.0 if finding.source == FindingSource.LLM else 0.0
    src_osv = 1.0 if finding.source == FindingSource.OSV else 0.0
    src_fused = 1.0 if finding.source == FindingSource.FUSED else 0.0

    has_cve_id = 0.0
    if finding.rule_id:
        if finding.rule_id.upper().startswith("CVE-"):
            has_cve_id = 1.0

    return {
        "severity": severity_map.get(finding.severity, 0.0),
        "src_semgrep": src_semgrep,
        "src_llm": src_llm,
        "src_osv": src_osv,
        "src_fused": src_fused,
        "confidence": float(finding.confidence),
        "has_rule_id": 1.0 if finding.rule_id else 0.0,
        "has_cve_id": has_cve_id
    }

class AdaptivePrioritizer:
    """
    Epsilon-greedy contextual bandit with simple linear weights for finding prioritization.
    """
    def __init__(self, epsilon: float = 0.1):
        self.epsilon = epsilon
        self.weights: Dict[str, float] = {
            "severity": 0.1,
            "src_semgrep": 0.1,
            "src_llm": 0.1,
            "src_osv": 0.1,
            "src_fused": 0.1,
            "confidence": 0.1,
            "has_rule_id": 0.1,
            "has_cve_id": 0.1
        }
        self.learning_rate = 0.01

    def get_priority_adjustment(self, finding: Finding) -> float:
        """
        Return a multiplier (0.1 to 2.0) for the finding's risk score.
        """
        features = feature_vector(finding)
        
        # Epsilon-greedy exploration
        if random.random() < self.epsilon:
            return random.uniform(0.1, 2.0)
            
        # Exploit: Calculate score based on linear weights
        score = sum(features.get(k, 0.0) * w for k, w in self.weights.items())
        
        # Simple clipping to [0.1, 2.0]
        adjustment = max(0.1, min(2.0, 1.0 + score))
        return adjustment

    def update(self, finding: Finding, reward: float) -> None:
        """
        Update weights based on feedback (reward: +1 for accepted, -1 for rejected).
        """
        features = feature_vector(finding)
        for k in self.weights:
            if k in features:
                # Simple stochastic gradient descent update rule
                self.weights[k] += self.learning_rate * reward * features[k]

    def save(self, path: str = ".tmp/rl_weights.json") -> None:
        """Persist weights to a JSON file."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w") as f:
                json.dump({
                    "epsilon": self.epsilon,
                    "weights": self.weights
                }, f, indent=2)
            logger.info(f"Saved weights to {path}")
        except Exception as e:
            logger.error(f"Failed to save weights to {path}: {e}")

    def load(self, path: str = ".tmp/rl_weights.json") -> None:
        """Load weights from a JSON file."""
        if not os.path.exists(path):
            logger.warning(f"Weights file {path} does not exist, using defaults.")
            return
            
        try:
            with open(path, "r") as f:
                data = json.load(f)
                self.epsilon = data.get("epsilon", self.epsilon)
                loaded_weights = data.get("weights", {})
                for k in self.weights:
                    if k in loaded_weights:
                        self.weights[k] = loaded_weights[k]
            logger.info(f"Loaded weights from {path}")
        except Exception as e:
            logger.error(f"Failed to load weights from {path}: {e}")
