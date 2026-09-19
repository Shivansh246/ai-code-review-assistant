from typing import List, Dict
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
        
    if getattr(finding, "id", None):
        finding_risk_scores[finding.id] = risk_score
        
    return risk_score

def rank_findings(findings: List[Finding]) -> List[Finding]:
    """
    Sorts findings by their risk score in descending order.
    """
    for finding in findings:
        score_finding(finding)
        
    return sorted(
        findings,
        key=lambda f: finding_risk_scores.get(getattr(f, "id", ""), 0.0),
        reverse=True
    )
