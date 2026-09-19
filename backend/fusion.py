import logging
from typing import List
from models import Finding, FindingSource, Severity

logger = logging.getLogger(__name__)

def _similar_titles(title1: str, title2: str) -> bool:
    if not title1 or not title2:
        return False
    words1 = set(title1.lower().split())
    words2 = set(title2.lower().split())
    if not words1 or not words2:
        return False
    overlap = len(words1.intersection(words2))
    ratio = overlap / min(len(words1), len(words2))
    return ratio > 0.5

def _overlap_ranges(f1: Finding, f2: Finding) -> bool:
    if f1.line_start is None or f1.line_end is None or f2.line_start is None or f2.line_end is None:
        return False
    return max(f1.line_start, f2.line_start) <= min(f1.line_end, f2.line_end)

def _get_severity_val(sev: Severity) -> int:
    vals = {
        Severity.critical: 5,
        Severity.high: 4,
        Severity.medium: 3,
        Severity.low: 2,
        Severity.info: 1
    }
    return vals.get(sev, 0)

def _merge_findings(f1: Finding, f2: Finding) -> Finding:
    # Use higher severity
    sev1 = _get_severity_val(f1.severity)
    sev2 = _get_severity_val(f2.severity)
    new_sev = f1.severity if sev1 >= sev2 else f2.severity

    # Combine confidence: 1 - (1-c1)*(1-c2)
    c1 = getattr(f1, "confidence", 1.0)
    c2 = getattr(f2, "confidence", 1.0)
    new_conf = 1.0 - (1.0 - c1) * (1.0 - c2)

    # Specific line range
    range1 = (f1.line_end - f1.line_start) if (f1.line_end is not None and f1.line_start is not None) else float('inf')
    range2 = (f2.line_end - f2.line_start) if (f2.line_end is not None and f2.line_start is not None) else float('inf')
    
    if range1 <= range2:
        new_start = f1.line_start
        new_end = f1.line_end
    else:
        new_start = f2.line_start
        new_end = f2.line_end

    new_desc = f"{f1.description}\n\nMerged with:\n{f2.description}"

    merged = Finding(
        id=f1.id,
        file=f1.file,
        line_start=new_start,
        line_end=new_end,
        title=f1.title,
        description=new_desc,
        severity=new_sev,
        source=FindingSource.fused,
        confidence=new_conf,
        rule_id=getattr(f1, 'rule_id', None) or getattr(f2, 'rule_id', None),
        cve_id=getattr(f1, 'cve_id', None) or getattr(f2, 'cve_id', None),
        package_name=getattr(f1, 'package_name', None) or getattr(f2, 'package_name', None),
        package_version=getattr(f1, 'package_version', None) or getattr(f2, 'package_version', None),
    )
    return merged

async def fuse_findings(findings: List[Finding]) -> List[Finding]:
    """
    Deduplicates and merges findings from multiple sources.
    """
    if not findings:
        return []

    fused: List[Finding] = []
    fused_count = 0

    for finding in findings:
        merged = False
        for i, existing in enumerate(fused):
            is_duplicate = False
            if existing.file == finding.file:
                existing_rule = getattr(existing, 'rule_id', None)
                finding_rule = getattr(finding, 'rule_id', None)
                existing_cve = getattr(existing, 'cve_id', None)
                finding_cve = getattr(finding, 'cve_id', None)
                
                # Rule 1 & 2: Explicit ID check (CVE IDs take priority, then Rule IDs)
                if existing_cve and finding_cve:
                    if existing_cve == finding_cve:
                        is_duplicate = True
                    else:
                        is_duplicate = False
                elif existing_rule and finding_rule:
                    if existing_rule == finding_rule:
                        if _overlap_ranges(existing, finding) or (existing.line_start == 1 and existing.line_end == 1 and finding.line_start == 1 and finding.line_end == 1):
                            is_duplicate = True
                    else:
                        is_duplicate = False
                # Rule 3: Cross-source or missing IDs (e.g. LLM + Semgrep)
                elif _overlap_ranges(existing, finding) and _similar_titles(existing.title, finding.title):
                    is_duplicate = True
            
            if is_duplicate:
                fused[i] = _merge_findings(existing, finding)
                fused_count += 1
                merged = True
                break
        
        if not merged:
            fused.append(finding)

    logger.info(f"Fused {fused_count} findings.")

    # Sort by severity (critical first), then confidence (descending)
    fused.sort(key=lambda f: (_get_severity_val(f.severity), getattr(f, "confidence", 1.0)), reverse=True)
    
    return fused
