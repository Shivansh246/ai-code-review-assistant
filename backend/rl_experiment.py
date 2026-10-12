"""
EXPERIMENTAL MODULE: rl_experiment
Implements a Discrete-Action Epsilon-Greedy Contextual Bandit for adaptive finding prioritization.

Key Architecture:
- Discrete Action Space A = [0.2, 0.6, 1.0, 1.4, 1.8] (risk score multipliers)
- Context Representation: 16-dimensional feature vector (severity, source one-hot, confidence, rule hash buckets)
- Reward Prediction Q(x, a) = w_a^T x per candidate action
- Action Association: Recorded action per finding_id ensures feedback (r = +1.0 accepted, -1.0 rejected) updates the selected action's model
- Action-Conditioned Update: SGD with prediction error delta = r - Q(x, a_selected)
"""

import hashlib
import json
import logging
import math
import os
import random
from typing import Dict, List, Optional, Tuple

from models import Finding, FindingSource, Severity

logger = logging.getLogger(__name__)

NUM_RULE_BUCKETS = 8
DISCRETE_ACTIONS: List[float] = [0.2, 0.6, 1.0, 1.4, 1.8]


def _get_rule_bucket(rule_id: str) -> int:
    """
    Computes a deterministic hash bucket index (0..7) for a rule_id.
    Uses MD5 digest to ensure stability across process restarts.

    Collision limitation: Distinct rule IDs may hash to the same bucket modulo 8.
    """
    digest = hashlib.md5(rule_id.encode("utf-8")).hexdigest()
    return int(digest, 16) % NUM_RULE_BUCKETS


def feature_vector(finding: Finding) -> Dict[str, float]:
    """
    Extracts an 16-dimensional feature vector from a Finding instance.
    """
    severity_map = {
        Severity.critical: 4.0,
        Severity.high: 3.0,
        Severity.medium: 2.0,
        Severity.low: 1.0,
        Severity.info: 0.0,
    }

    # Canonical lowercase enum member comparisons
    src_semgrep = 1.0 if finding.source == FindingSource.semgrep else 0.0
    src_llm = 1.0 if finding.source == FindingSource.llm else 0.0
    src_osv = 1.0 if finding.source == FindingSource.osv else 0.0
    src_fused = 1.0 if finding.source == FindingSource.fused else 0.0

    has_cve_id = 0.0
    if finding.cve_id and finding.cve_id.strip():
        has_cve_id = 1.0
    elif finding.rule_id and finding.rule_id.upper().startswith("CVE-"):
        has_cve_id = 1.0

    rule_buckets = {f"rule_bucket_{i}": 0.0 for i in range(NUM_RULE_BUCKETS)}
    if finding.rule_id and finding.rule_id.strip():
        bucket_idx = _get_rule_bucket(finding.rule_id.strip())
        rule_buckets[f"rule_bucket_{bucket_idx}"] = 1.0

    conf_val = float(finding.confidence) if finding.confidence is not None else 1.0
    conf_val = max(0.0, min(1.0, conf_val))

    features = {
        "severity": severity_map.get(finding.severity, 0.0),
        "src_semgrep": src_semgrep,
        "src_llm": src_llm,
        "src_osv": src_osv,
        "src_fused": src_fused,
        "confidence": conf_val,
        "has_rule_id": 1.0 if finding.rule_id and finding.rule_id.strip() else 0.0,
        "has_cve_id": has_cve_id,
        **rule_buckets
    }
    return features


class AdaptivePrioritizer:
    """
    Discrete-Action Contextual Bandit for finding prioritization.
    """
    VERSION = "1.3"

    def __init__(
        self,
        epsilon: float = 0.1,
        learning_rate: float = 0.01,
        seed: Optional[int] = None,
        eval_mode: bool = False,
        evidence_threshold: int = 5
    ):
        self.epsilon = epsilon
        self.learning_rate = learning_rate
        self.eval_mode = eval_mode
        self.evidence_threshold = max(1, int(evidence_threshold))
        self.update_count: int = 0
        self.rng = random.Random(seed) if seed is not None else random.Random()
        self.actions: List[float] = list(DISCRETE_ACTIONS)

        # Action history mapping: finding_id -> list of selected actions (FIFO order)
        self.action_history: Dict[str, List[float]] = {}

        # Per-action linear weight vectors: action_str -> {feature_name -> weight}
        self.action_weights: Dict[str, Dict[str, float]] = {}
        for a in self.actions:
            act_str = f"{a:.1f}"
            self.action_weights[act_str] = self._default_weights_for_action(a)

    def _default_weights_for_action(self, action: float) -> Dict[str, float]:
        """
        Initializes uninformative prior weights (all 0.0) for an action.
        Untrained weights do not represent learned preferences.
        """
        weights = {
            "severity": 0.0,
            "src_semgrep": 0.0,
            "src_llm": 0.0,
            "src_osv": 0.0,
            "src_fused": 0.0,
            "confidence": 0.0,
            "has_rule_id": 0.0,
            "has_cve_id": 0.0,
        }
        for i in range(NUM_RULE_BUCKETS):
            weights[f"rule_bucket_{i}"] = 0.0
        return weights

    @property
    def is_cold_start(self) -> bool:
        """
        Returns True if the prioritizer is operating under cold-start conditions:
        either feedback update count is below the evidence threshold, or all weights are zero.
        """
        return (self.update_count < self.evidence_threshold) or self._are_all_weights_zero()

    def _are_all_weights_zero(self) -> bool:
        """Checks if all weight vectors across all discrete actions are effectively zero."""
        for weights in self.action_weights.values():
            for w in weights.values():
                if abs(w) > 1e-6:
                    return False
        return True

    def predict_reward(self, finding: Finding, action: float) -> float:
        """
        Predicts expected reward Q(x, a) = w_a^T x for a candidate action.
        """
        act_str = f"{action:.1f}"
        if act_str not in self.action_weights:
            return 0.0

        features = feature_vector(finding)
        weights = self.action_weights[act_str]
        return sum(features.get(k, 0.0) * w for k, w in weights.items())

    def record_action(self, finding_id: str, action: float) -> None:
        """
        Explicitly associates a finding_id with the action that generated its ranking.
        Stores actions in a FIFO list per finding_id so repeated reviews do not overwrite
        actions needed for earlier feedback attribution.
        """
        if finding_id and finding_id.strip():
            fid = finding_id.strip()
            if fid not in self.action_history:
                self.action_history[fid] = []
            self.action_history[fid].append(float(action))
            if len(self.action_history[fid]) > 20:
                self.action_history[fid].pop(0)

    def get_recorded_action(self, finding_id: str, consume: bool = False) -> Optional[float]:
        """
        Retrieves the action associated with a finding_id if available.
        If consume=True, pops the oldest recorded action (FIFO) for feedback attribution.
        """
        if not finding_id:
            return None
        fid = finding_id.strip()
        actions = self.action_history.get(fid)
        if not actions:
            return None
        if consume:
            act = actions.pop(0)
            if not actions:
                self.action_history.pop(fid, None)
            return act
        return actions[0]

    def get_priority_adjustment(self, finding: Finding) -> float:
        """
        Selects a risk score multiplier action from DISCRETE_ACTIONS.

        Cold-Start Policy:
        - When in cold-start (update_count < evidence_threshold or all weights zero):
          - In evaluation mode (eval_mode=True): deterministically selects 1.0 (neutral baseline).
          - In training mode (eval_mode=False): explores with probability epsilon, else selects 1.0.

        Learned Policy:
        - Evaluates predicted reward Q(x, a) = w_a^T x for all candidate actions.
        - Greedy action maximizes predicted reward; ties broken toward neutral 1.0 (-abs(a - 1.0)).
        - In evaluation mode: deterministically selects greedy action.
        - In training mode: epsilon-greedy exploration.

        Records the selected action for later feedback attribution.
        """
        if self.is_cold_start:
            if not self.eval_mode and self.epsilon > 0.0 and self.rng.random() < self.epsilon:
                selected_action = self.rng.choice(self.actions)
            else:
                selected_action = 1.0
        else:
            predicted_q = {a: self.predict_reward(finding, a) for a in self.actions}
            best_action = max(self.actions, key=lambda a: (predicted_q[a], -abs(a - 1.0)))

            if not self.eval_mode and self.epsilon > 0.0 and self.rng.random() < self.epsilon:
                selected_action = self.rng.choice(self.actions)
            else:
                selected_action = best_action

        fid = getattr(finding, "id", None)
        if fid:
            self.record_action(fid, selected_action)

        return selected_action

    def update(self, finding: Finding, reward: float, action: Optional[float] = None) -> None:
        """
        Updates the model weights for the action associated with this finding.
        Uses prediction error delta = r - Q(x, a_selected) and SGD.
        """
        if not math.isfinite(reward):
            logger.warning(f"Invalid non-finite reward {reward} received, update skipped.")
            return

        fid = getattr(finding, "id", None)
        target_action = action

        # Look up and consume recorded action if not explicitly supplied
        if target_action is None and fid:
            target_action = self.get_recorded_action(fid, consume=True)

        # Safety: Do NOT silently guess or update action 1.0 if association is missing!
        if target_action is None:
            logger.warning(
                f"No recorded action associated with finding ID '{fid}'. "
                "Cannot perform credit assignment without knowing the taken action; skipping update."
            )
            return

        # Snap to closest discrete action
        closest_action = min(self.actions, key=lambda a: abs(a - target_action))
        act_str = f"{closest_action:.1f}"

        features = feature_vector(finding)
        current_pred = self.predict_reward(finding, closest_action)
        delta = reward - current_pred

        if not math.isfinite(delta):
            return

        # Action-conditioned SGD update applied ONLY to the selected action's weights
        weights = self.action_weights[act_str]
        for k in weights:
            if k in features and math.isfinite(features[k]):
                updated_val = weights[k] + self.learning_rate * delta * features[k]
                if math.isfinite(updated_val):
                    weights[k] = max(-10.0, min(10.0, updated_val))

        self.update_count += 1

    def save(self, path: str = ".tmp/rl_weights.json") -> None:
        """Persist weights, metadata, and actions to JSON."""
        abs_path = os.path.abspath(path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        try:
            payload = {
                "version": self.VERSION,
                "epsilon": self.epsilon,
                "learning_rate": self.learning_rate,
                "evidence_threshold": self.evidence_threshold,
                "update_count": self.update_count,
                "actions": self.actions,
                "action_weights": self.action_weights
            }
            with open(abs_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            logger.info(f"Saved RL weights (v{self.VERSION}) to {abs_path}")
        except Exception as e:
            logger.error(f"Failed to save RL weights to {path}: {e}")

    def load(self, path: str = ".tmp/rl_weights.json") -> None:
        """Load weights from JSON with version validation and error recovery."""
        abs_path = os.path.abspath(path)
        if not os.path.exists(abs_path):
            logger.warning(f"Weights file {abs_path} does not exist, using defaults.")
            return

        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                logger.warning(f"Invalid weight file format in {abs_path}, ignoring.")
                return

            self.epsilon = float(data.get("epsilon", self.epsilon))
            self.learning_rate = float(data.get("learning_rate", self.learning_rate))
            self.evidence_threshold = int(data.get("evidence_threshold", self.evidence_threshold))
            self.update_count = int(data.get("update_count", 0))

            loaded_action_weights = data.get("action_weights", {})
            if isinstance(loaded_action_weights, dict) and loaded_action_weights:
                for act_str, weights in loaded_action_weights.items():
                    if act_str in self.action_weights and isinstance(weights, dict):
                        for k in self.action_weights[act_str]:
                            if k in weights and math.isfinite(weights[k]):
                                self.action_weights[act_str][k] = float(weights[k])
            logger.info(f"Loaded RL weights from {abs_path}")
        except Exception as e:
            logger.error(f"Failed to load RL weights from {abs_path}: {e}")
