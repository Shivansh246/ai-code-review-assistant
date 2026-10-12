from typing import List, Dict, Optional, Tuple, Any
from models import Finding, FindingSource, Severity, FeedbackStatus

# Module-level dictionary to store computed risk scores keyed by finding.id
finding_risk_scores: Dict[str, float] = {}

def score_finding(finding: Finding) -> float:
    """
    Computes a 0-100 risk score for a finding based on severity, source, and confidence.
    """
    sev_weights = {
        Severity.critical: 1.0,
        Severity.high: 0.8,
        Severity.medium: 0.5,
        Severity.low: 0.2,
        Severity.info: 0.05
    }
    
    src_weights = {
        FindingSource.fused: 1.0,
        FindingSource.semgrep: 0.9,
        FindingSource.llm: 0.7,
        FindingSource.osv: 0.95
    }
    
    severity_weight = sev_weights.get(finding.severity, 0.05)
    source_weight = src_weights.get(finding.source, 0.5)
    confidence = getattr(finding, "confidence", 1.0)
    
    risk_score = severity_weight * source_weight * confidence * 100.0
    
    # Demote if rejected by user feedback
    if getattr(finding, "feedback_status", None) == FeedbackStatus.rejected:
        risk_score *= 0.1

    risk_score = round(risk_score, 2)
    risk_score = max(0.0, min(100.0, risk_score))
    finding.risk_score = risk_score
    if getattr(finding, "id", None):
        finding_risk_scores[finding.id] = risk_score

    return risk_score

def compute_adaptive_multiplier(finding: Finding, stats: Optional[dict] = None) -> float:
    """
    Computes a deterministic, bounded multiplier based on historical feedback stats.

    Formula:
        rejection_penalty = 1.0 / (1.0 + 0.5 * rejections)
        acceptance_boost = min(0.2, 0.05 * acceptances)
        multiplier = clamp(rejection_penalty + acceptance_boost, 0.1, 1.2)
    """
    if not stats or not isinstance(stats, dict):
        return 1.0

    rule_stats = stats.get("rule_stats", {})
    source_stats = stats.get("source_stats", {})

    rule_id = getattr(finding, "rule_id", None)
    source_val = getattr(finding, "source", None)
    if hasattr(source_val, "value"):
        source_val = source_val.value

    rejections = 0
    acceptances = 0

    if rule_id and rule_id in rule_stats:
        rejections += rule_stats[rule_id].get("rejected", 0)
        acceptances += rule_stats[rule_id].get("accepted", 0)

    if source_val and source_val in source_stats:
        rejections += source_stats[source_val].get("rejected", 0)
        acceptances += source_stats[source_val].get("accepted", 0)

    rejection_penalty = 1.0 / (1.0 + 0.5 * rejections)
    acceptance_boost = min(0.2, 0.05 * acceptances)

    multiplier = max(0.1, min(1.2, rejection_penalty + acceptance_boost))
    return multiplier

def score_finding_adaptive(finding: Finding, stats: Optional[dict] = None) -> float:
    """
    Computes a 0-100 adaptive risk score combining static scoring and historical feedback adjustment.
    """
    base_score = score_finding(finding)

    multiplier = compute_adaptive_multiplier(finding, stats)
    adaptive_score = max(0.0, min(100.0, round(base_score * multiplier, 2)))
    finding.risk_score = adaptive_score

    if getattr(finding, "id", None):
        finding_risk_scores[finding.id] = adaptive_score

    return adaptive_score

def _ranking_key(f: Finding) -> Tuple[float, int, str]:
    """Deterministic tie-breaking key: score desc, line_start asc, id desc."""
    score = getattr(f, "risk_score", None)
    if score is None:
        score = finding_risk_scores.get(getattr(f, "id", ""), 0.0)
    line = getattr(f, "line_start", 0) or 0
    fid = getattr(f, "id", "") or ""
    return (float(score), -int(line), str(fid))

def rank_findings(findings: List[Finding]) -> List[Finding]:
    """
    Sorts findings by their risk score in descending order with deterministic tie-breaking.
    """
    for finding in findings:
        score_finding(finding)

    return sorted(findings, key=_ranking_key, reverse=True)

def rank_findings_adaptive(findings: List[Finding], stats: Optional[dict] = None) -> List[Finding]:
    """
    Sorts findings by their adaptive risk score in descending order with deterministic tie-breaking.
    """
    for finding in findings:
        score_finding_adaptive(finding, stats)

    return sorted(findings, key=_ranking_key, reverse=True)

def score_finding_rl(
    finding: Finding,
    prioritizer: Any,
    review_id: Optional[str] = None
) -> Tuple[float, float]:
    """
    Computes risk score using the RL contextual bandit multiplier applied to the base static score.
    Returns (rl_score, action_multiplier).
    Clamps the final score to [0.0, 100.0].
    """
    base_score = score_finding(finding)
    action = prioritizer.get_priority_adjustment(finding)
    allowed_actions = getattr(prioritizer, "actions", [0.2, 0.6, 1.0, 1.4, 1.8])
    if action not in allowed_actions:
        action = 1.0

    rl_score = max(0.0, min(100.0, round(base_score * action, 2)))
    finding.risk_score = rl_score
    if getattr(finding, "id", None):
        finding_risk_scores[finding.id] = rl_score

    return rl_score, action

def rank_findings_rl(
    findings: List[Finding],
    prioritizer: Any,
    review_id: Optional[str] = None
) -> Tuple[List[Finding], Dict[str, float]]:
    """
    Ranks findings using RL contextual bandit multipliers with deterministic tie-breaking.
    Returns (ranked_findings, actions_applied_dict).
    """
    applied_actions: Dict[str, float] = {}
    for finding in findings:
        _, action = score_finding_rl(finding, prioritizer, review_id=review_id)
        if getattr(finding, "id", None):
            applied_actions[finding.id] = action

    ranked = sorted(findings, key=_ranking_key, reverse=True)
    return ranked, applied_actions

def compare_scoring_methods(
    findings: List[Finding],
    feedback_stats: Optional[dict] = None,
    prioritizer: Optional[Any] = None
) -> Dict[str, List[Finding]]:
    """
    Evaluation interface exposing three-way comparison on identical finding fixtures:
    - 'static': Baseline static ranking
    - 'adaptive': Phase 6A deterministic feedback ranking
    - 'rl': Phase 6B contextual bandit ranking (if prioritizer provided)
    """
    import copy
    results = {
        "static": rank_findings([copy.deepcopy(f) for f in findings]),
        "adaptive": rank_findings_adaptive([copy.deepcopy(f) for f in findings], feedback_stats),
    }
    if prioritizer is not None:
        ranked_rl, _ = rank_findings_rl([copy.deepcopy(f) for f in findings], prioritizer)
        results["rl"] = ranked_rl
    return results
