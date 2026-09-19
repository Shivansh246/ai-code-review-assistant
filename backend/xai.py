"""
xai.py
Explainable AI (XAI) subsystem for security findings.

Provides structured interpretability for security findings, including:
1. Evidence extraction from source code with line number preservation
2. Risk score breakdown and score factor analysis
3. Vulnerability patterns and remediation guidance
4. LLM semantic explanation with deterministic fallback handling
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from models import FeedbackStatus, Finding, FindingSource, Severity, TokenAttribution
import scoring

logger = logging.getLogger(__name__)

# ── Prompts ───────────────────────────────────────────────────────────────────

EXPLAIN_PROMPT = """\
You are a security engineer explaining a code vulnerability to a developer.

## Finding
- Title: {title}
- Severity: {severity}
- Description: {description}
- File: {file}
- Lines: {line_start}–{line_end}

## Source Code (with line numbers)
```
{numbered_code}
```

## Task
Provide a JSON object with exactly these fields:
1. "explanation": A clear 2–4 sentence explanation of WHY this is a security issue, what the attack vector is, and what the impact could be.
2. "token_attributions": An array of objects, each with "token" (a short code snippet, keyword, or identifier from the source that contributes to the vulnerability) and "importance" (float from -1.0 to 1.0, where positive means the token CONTRIBUTES to the vulnerability, negative means it MITIGATES). Include 3–8 tokens.
3. "highlighted_lines": An array of 1-based line numbers that are most relevant to this finding.

Return ONLY valid JSON. No markdown fences, no extra text.
"""

FIX_PROMPT = """\
You are a security engineer fixing a code vulnerability.

## Finding
- Title: {title}
- Severity: {severity}
- Description: {description}
- File: {file}
- Lines: {line_start}–{line_end}

## Source Code
```
{code}
```

## Task
Provide a JSON object with exactly these fields:
1. "fix_suggestion": The corrected code snippet that fixes the vulnerability. Include enough surrounding context so the developer can apply the fix.
2. "confidence": A float from 0.0 to 1.0 indicating how confident you are that this fix is correct and complete.

Return ONLY valid JSON. No markdown fences, no extra text.
"""


# ── Evidence Extraction ───────────────────────────────────────────────────────

def extract_evidence_snippet(
    file_path: str,
    line_start: int,
    line_end: int,
    context_lines: int = 2
) -> Tuple[str, List[int]]:
    """
    Safely reads source lines from file_path between line_start and line_end,
    including context_lines before and after. Preserves 1-based line numbers.

    Returns:
        (formatted_snippet, highlighted_line_numbers)
    """
    safe_start = max(1, line_start)
    safe_end = max(safe_start, line_end)

    if not file_path:
        fallback_lines = list(range(safe_start, safe_end + 1))
        return "Source snippet unavailable: no file path provided.", fallback_lines

    # Try resolving path
    target_path = Path(file_path)
    if not target_path.exists():
        cwd_path = Path.cwd() / file_path
        if cwd_path.exists():
            target_path = cwd_path

    if not target_path.exists() or not target_path.is_file():
        fallback_lines = list(range(safe_start, safe_end + 1))
        return f"Source snippet unavailable: file '{file_path}' not found.", fallback_lines

    try:
        content = target_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        total_lines = len(lines)

        if total_lines == 0:
            return "Source file is empty.", [1]

        # Clamp range to file length
        clamped_start = min(safe_start, total_lines)
        clamped_end = min(safe_end, total_lines)

        start_idx = max(0, clamped_start - 1 - context_lines)
        end_idx = min(total_lines, clamped_end + context_lines)

        snippet_lines = []
        for i in range(start_idx, end_idx):
            line_num = i + 1
            snippet_lines.append(f"{line_num:4d} | {lines[i]}")

        highlighted = list(range(clamped_start, clamped_end + 1))
        return "\n".join(snippet_lines), highlighted

    except Exception as e:
        logger.warning(f"Failed to read source file '{file_path}': {e}")
        fallback_lines = list(range(safe_start, safe_end + 1))
        return f"Source snippet unavailable: {e}", fallback_lines


# ── Risk Score Factor Explanation ──────────────────────────────────────────────

def compute_score_explanation(finding: Finding) -> Tuple[float, Dict[str, float], str]:
    """
    Computes risk score and breakdown of score factors based on scoring logic.

    Returns:
        (risk_score, score_factors_dict, risk_explanation_text)
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
        FindingSource.osv: 0.95,
        FindingSource.semgrep: 0.9,
        FindingSource.llm: 0.7
    }

    sev_w = sev_weights.get(finding.severity, 0.05)
    src_w = src_weights.get(finding.source, 0.5)
    conf = getattr(finding, "confidence", 1.0)
    if conf is None:
        conf = 1.0
    conf = max(0.0, min(1.0, float(conf)))

    fb_status = getattr(finding, "feedback_status", None)
    fb_factor = 0.1 if fb_status == FeedbackStatus.rejected else 1.0

    raw_score = sev_w * src_w * conf * 100.0 * fb_factor
    risk_score = round(raw_score, 2)

    factors = {
        "severity_weight": sev_w,
        "source_weight": src_w,
        "confidence": conf,
        "feedback_factor": fb_factor,
        "risk_score": risk_score
    }

    explanation_str = (
        f"Risk Score: {risk_score}/100 "
        f"(Severity weight: {sev_w} [{finding.severity.value}], "
        f"Source weight: {src_w} [{finding.source.value}], "
        f"Confidence: {conf:.2f}"
        + (f", Feedback penalty: {fb_factor}" if fb_factor < 1.0 else "") + ")"
    )

    return risk_score, factors, explanation_str


# ── Remediation Rationale ──────────────────────────────────────────────────────

def get_remediation_rationale(finding: Finding) -> str:
    """
    Generates deterministic remediation rationale based on rule_id, cve_id, or title.
    """
    title_lower = (finding.title or "").lower()
    rule_lower = (finding.rule_id or "").lower()

    if finding.package_name or finding.cve_id:
        pkg = finding.package_name or "dependency"
        cve = finding.cve_id or "known vulnerability"
        ver = f" (current: {finding.package_version})" if finding.package_version else ""
        return f"Upgrade package '{pkg}'{ver} to a patched release to address {cve}."

    if "sqli" in rule_lower or "sql" in title_lower:
        return "Use parameterized queries, prepared statements, or ORM parameter binding instead of string concatenation."

    if "md5" in rule_lower or "hash" in title_lower or "sha1" in rule_lower:
        return "Replace legacy hashing algorithms (MD5/SHA1) with secure alternatives (SHA-256 or bcrypt/argon2 for passwords)."

    if "secret" in rule_lower or "key" in title_lower or "credential" in rule_lower:
        return "Extract hardcoded credentials into environment variables or a secret manager."

    if "command" in rule_lower or "subprocess" in title_lower or "exec" in rule_lower:
        return "Avoid passing unsanitized input to subprocess execution shell commands. Use argument lists instead of shell=True."

    return f"Inspect code at line {finding.line_start} in '{finding.file}' and enforce defensive input validation and security controls."


# ── Helpers for Formatting and Token Attributions ──────────────────────────────

def _number_lines(code: str) -> str:
    """Add 1-based line numbers to code for the prompt."""
    lines = code.split("\n")
    return "\n".join(f"{i + 1:4d} | {line}" for i, line in enumerate(lines))


def _extract_json(text: str) -> dict:
    """Robustly extract a JSON object from LLM output."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    logger.warning("Failed to parse JSON from LLM response: %s", text[:200])
    return {}


def _generate_deterministic_tokens(finding: Finding, snippet: str) -> List[TokenAttribution]:
    """Generates fallback token attributions from key terms in title or snippet."""
    tokens = []
    keywords = re.findall(r"\b[a-zA-Z0-9_\-\.]{3,}\b", finding.title or "")
    for kw in set(keywords[:4]):
        tokens.append(TokenAttribution(token=kw, importance=0.8))

    if finding.rule_id:
        tokens.append(TokenAttribution(token=finding.rule_id, importance=0.9))

    return tokens[:6]


# ── Structured XAI Contract ────────────────────────────────────────────────────

async def generate_structured_explanation(
    finding: Finding,
    code_context: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generates a complete, structured XAI explanation contract for a finding.
    Never throws an exception; falls back gracefully.
    """
    # 1. Evidence extraction
    if code_context and code_context.strip():
        evidence_snippet = _number_lines(code_context)
        highlighted_lines = list(range(max(1, finding.line_start), max(finding.line_start, finding.line_end) + 1))
    else:
        evidence_snippet, highlighted_lines = extract_evidence_snippet(
            finding.file, finding.line_start, finding.line_end
        )

    # 2. Score factors
    risk_score, score_factors, score_exp = compute_score_explanation(finding)

    # 3. Remediation rationale
    remediation_rationale = get_remediation_rationale(finding)

    # 4. Attempt LLM semantic explanation if available
    llm_explanation = None
    token_attributions: List[TokenAttribution] = []

    try:
        from scanner_llm import _call_llm
        prompt = EXPLAIN_PROMPT.format(
            title=finding.title,
            severity=finding.severity.value if hasattr(finding.severity, "value") else str(finding.severity),
            description=finding.description,
            file=finding.file,
            line_start=finding.line_start,
            line_end=finding.line_end,
            numbered_code=evidence_snippet,
        )

        raw = await _call_llm(prompt)
        if raw:
            data = _extract_json(raw)
            if isinstance(data, dict) and "explanation" in data:
                llm_explanation = str(data["explanation"]).strip()
                raw_attrs = data.get("token_attributions", [])
                if isinstance(raw_attrs, list):
                    for attr in raw_attrs:
                        if isinstance(attr, dict) and "token" in attr and "importance" in attr:
                            try:
                                imp = max(-1.0, min(1.0, float(attr["importance"])))
                                token_attributions.append(TokenAttribution(token=str(attr["token"]), importance=imp))
                            except (ValueError, TypeError):
                                pass

                raw_hl = data.get("highlighted_lines", [])
                if isinstance(raw_hl, list) and raw_hl:
                    valid_hl = [int(l) for l in raw_hl if isinstance(l, (int, float)) and l > 0]
                    if valid_hl:
                        highlighted_lines = valid_hl

    except Exception as e:
        logger.warning(f"LLM explanation call skipped or failed: {e}")

    # 5. Deterministic fallback if LLM is unavailable or failed
    if not llm_explanation:
        llm_explanation = (
            f"Vulnerability: {finding.title}. {finding.description}\n\n"
            f"Score Analysis: {score_exp}\n\n"
            f"Remediation Guidance: {remediation_rationale}"
        )
        if not token_attributions:
            token_attributions = _generate_deterministic_tokens(finding, evidence_snippet)

    severity_str = finding.severity.value if hasattr(finding.severity, "value") else str(finding.severity)

    return {
        "finding_id": getattr(finding, "id", ""),
        "title": finding.title,
        "severity": severity_str,
        "rule_id": finding.rule_id,
        "cve_id": finding.cve_id,
        "explanation": llm_explanation,
        "evidence_snippet": evidence_snippet,
        "highlighted_lines": highlighted_lines,
        "token_attributions": token_attributions,
        "risk_score": risk_score,
        "score_factors": score_factors,
        "remediation_rationale": remediation_rationale
    }


async def generate_explanation(
    finding: Finding,
    code_context: Optional[str] = None,
) -> Tuple[str, List[TokenAttribution], List[int]]:
    """
    Generates an XAI explanation for a finding (backwards-compatible 3-tuple signature).

    Returns:
        (explanation_text, token_attributions, highlighted_line_numbers)
    """
    res = await generate_structured_explanation(finding, code_context=code_context)
    return res["explanation"], res["token_attributions"], res["highlighted_lines"]


async def generate_fix(
    finding: Finding,
    code_context: str,
) -> Tuple[str, float]:
    """
    Generate a fix suggestion for a finding using the LLM (or deterministic fallback).

    Returns:
        (fix_suggestion_code, confidence)
    """
    try:
        from scanner_llm import _call_llm
        prompt = FIX_PROMPT.format(
            title=finding.title,
            severity=finding.severity.value if hasattr(finding.severity, "value") else str(finding.severity),
            description=finding.description,
            file=finding.file,
            line_start=finding.line_start,
            line_end=finding.line_end,
            code=code_context,
        )

        raw = await _call_llm(prompt)
        if raw:
            data = _extract_json(raw)
            if isinstance(data, dict):
                fix = data.get("fix_suggestion", "")
                confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
                if fix:
                    return fix, confidence

    except Exception as e:
        logger.warning(f"Fix generation call failed: {e}")

    # Fallback remediation code recommendation
    rationale = get_remediation_rationale(finding)
    fallback_fix = f"# Suggested Fix Rationale:\n# {rationale}\n\n# Review and update affected lines {finding.line_start}-{finding.line_end} in {finding.file}"
    return fallback_fix, 0.5
