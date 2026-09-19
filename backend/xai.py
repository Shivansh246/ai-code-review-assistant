"""
xai.py
Explainable AI module — generates human-readable explanations and
token-level attributions for security findings.

Uses the same LLM backend as scanner_llm.py, but with specialised prompts
that request attribution-style output.

Week 5 feature: XAI highlights in the VS Code editor.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

from models import Finding, TokenAttribution

logger = logging.getLogger(__name__)

# ── Explanation via LLM ───────────────────────────────────────────────────────

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
2. "token_attributions": An array of objects, each with "token" (a short code snippet, keyword, or identifier from the source that contributes to the vulnerability) and "importance" (float from -1.0 to 1.0, where positive means the token CONTRIBUTES to the vulnerability, negative means it MITIGATES).  Include 3–8 tokens.
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


def _number_lines(code: str) -> str:
    """Add 1-based line numbers to code for the prompt."""
    lines = code.split("\n")
    return "\n".join(f"{i + 1:4d} | {line}" for i, line in enumerate(lines))


def _extract_json(text: str) -> dict:
    """Robustly extract a JSON object from LLM output."""
    # Strip markdown code fences if present
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    logger.warning("Failed to parse JSON from LLM response: %s", text[:200])
    return {}


async def generate_explanation(
    finding: Finding,
    code_context: str,
) -> tuple[str, list[TokenAttribution], list[int]]:
    """
    Generate an XAI explanation for a finding using the LLM.

    Returns:
        (explanation_text, token_attributions, highlighted_line_numbers)
    """
    # Import here to avoid circular deps and allow scanner_llm to be
    # the single source of truth for LLM client management.
    from scanner_llm import _call_llm

    numbered = _number_lines(code_context)
    prompt = EXPLAIN_PROMPT.format(
        title=finding.title,
        severity=finding.severity.value,
        description=finding.description,
        file=finding.file,
        line_start=finding.line_start,
        line_end=finding.line_end,
        numbered_code=numbered,
    )

    try:
        raw = await _call_llm(prompt)
        data = _extract_json(raw)

        explanation = data.get("explanation", finding.description)
        raw_attrs = data.get("token_attributions", [])
        highlighted = data.get("highlighted_lines", [])

        # Parse token attributions
        attributions: list[TokenAttribution] = []
        for attr in raw_attrs:
            if isinstance(attr, dict) and "token" in attr and "importance" in attr:
                importance = max(-1.0, min(1.0, float(attr["importance"])))
                attributions.append(
                    TokenAttribution(token=attr["token"], importance=importance)
                )

        # Validate highlighted lines
        valid_lines = [
            int(l) for l in highlighted
            if isinstance(l, (int, float)) and l > 0
        ]

        return explanation, attributions, valid_lines

    except Exception as e:
        logger.error("XAI explanation generation failed: %s", e)
        # Graceful fallback — return finding's own description
        return (
            finding.description,
            [],
            list(range(finding.line_start, finding.line_end + 1)),
        )


async def generate_fix(
    finding: Finding,
    code_context: str,
) -> tuple[str, float]:
    """
    Generate a fix suggestion for a finding using the LLM.

    Returns:
        (fix_suggestion_code, confidence)
    """
    from scanner_llm import _call_llm

    prompt = FIX_PROMPT.format(
        title=finding.title,
        severity=finding.severity.value,
        description=finding.description,
        file=finding.file,
        line_start=finding.line_start,
        line_end=finding.line_end,
        code=code_context,
    )

    try:
        raw = await _call_llm(prompt)
        data = _extract_json(raw)

        fix = data.get("fix_suggestion", "")
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))

        if not fix:
            return "No fix suggestion available.", 0.0

        return fix, confidence

    except Exception as e:
        logger.error("Fix generation failed: %s", e)
        return "Fix generation failed. Please review manually.", 0.0
