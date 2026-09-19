"""
models.py
Pydantic v2 models that mirror the TypeScript contracts in
vscode-extension/src/types.ts.

IMPORTANT: keep this file in sync with types.ts.
If you add or rename a field here, update types.ts (and vice-versa).
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────

class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"


class FindingSource(str, Enum):
    semgrep = "semgrep"
    llm = "llm"
    osv = "osv"
    fused = "fused"


class FeedbackStatus(str, Enum):
    accepted = "accepted"
    rejected = "rejected"
    pending = "pending"


class VoiceCommandType(str, Enum):
    review = "review"
    explain = "explain"
    show_critical = "show_critical"
    generate_fix = "generate_fix"
    accept = "accept"
    reject = "reject"
    unknown = "unknown"


# ── Token Attribution (XAI) ───────────────────────────────────────────────────

class TokenAttribution(BaseModel):
    """Token-level importance from XAI (Captum / SHAP / LLM-based)."""
    token: str
    importance: float = Field(ge=-1.0, le=1.0)


# ── Finding ───────────────────────────────────────────────────────────────────

class Finding(BaseModel):
    """A single security finding returned by the analysis pipeline."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: FindingSource
    severity: Severity
    title: str
    description: str
    file: str                                   # relative path
    line_start: int
    line_end: int
    column_start: Optional[int] = None
    column_end: Optional[int] = None
    confidence: float = Field(ge=0.0, le=1.0)
    rule_id: Optional[str] = None               # Semgrep rule id
    cve_id: Optional[str] = None                # CVE/GHSA id
    package_name: Optional[str] = None
    package_version: Optional[str] = None
    explanation: Optional[str] = None            # XAI / LLM explanation
    fix_suggestion: Optional[str] = None
    token_attributions: Optional[list[TokenAttribution]] = None
    feedback_status: Optional[FeedbackStatus] = None


# ── Request / Response: /review ───────────────────────────────────────────────

class ReviewRequest(BaseModel):
    file_path: str
    content: str


class RepoReviewRequest(BaseModel):
    repo_path: str


class ReviewResponse(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    file_path: str
    findings: list[Finding] = []
    scan_duration_ms: float = 0.0
    sources_used: list[FindingSource] = []


# ── Request / Response: /explain ──────────────────────────────────────────────

class ExplainRequest(BaseModel):
    finding_id: str


class ExplainResponse(BaseModel):
    finding_id: str
    explanation: str
    token_attributions: Optional[list[TokenAttribution]] = None
    highlighted_lines: list[int] = []
    rule_id: Optional[str] = None
    severity: Optional[str] = None
    risk_score: Optional[float] = None
    score_factors: Optional[dict[str, float]] = None
    evidence_snippet: Optional[str] = None
    remediation_rationale: Optional[str] = None


# ── Request / Response: /fix ──────────────────────────────────────────────────

class FixRequest(BaseModel):
    finding_id: str
    content: str


class FixResponse(BaseModel):
    finding_id: str
    fix_suggestion: str
    confidence: float = Field(ge=0.0, le=1.0)


# ── Feedback ──────────────────────────────────────────────────────────────────

class FeedbackEvent(BaseModel):
    finding_id: str
    status: FeedbackStatus
    timestamp: str                              # ISO 8601
    comment: Optional[str] = None


# ── Voice ─────────────────────────────────────────────────────────────────────

class VoiceCommand(BaseModel):
    transcript: str
    command: VoiceCommandType
    code_context: Optional[str] = None
    file_path: Optional[str] = None
    finding_id: Optional[str] = None
    finding_index: Optional[int] = None
    severity: Optional[str] = None
    target: Optional[str] = None
