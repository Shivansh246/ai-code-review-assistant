"""
main.py
FastAPI backend for the AI Code Review Assistant.

Orchestrates the analysis pipeline:
    Request → [Semgrep, LLM, OSV] → Fusion → Scoring → Response

Endpoints match the contracts defined in vscode-extension/src/types.ts.

Usage:
    cd backend
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from models import (
    ExplainRequest,
    ExplainResponse,
    FeedbackEvent,
    FindingSource,
    FixRequest,
    FixResponse,
    ReviewRequest,
    ReviewResponse,
    RepoReviewRequest,
    VoiceCommand,
)

import feedback
import fusion
import scoring
import scanner_semgrep
import scanner_llm
import scanner_osv
import xai
from rl_experiment import AdaptivePrioritizer

# ── Bootstrap ─────────────────────────────────────────────────────────────────

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("ai-review")

# Adaptive prioritizer (RL experiment)
prioritizer = AdaptivePrioritizer(epsilon=0.1)
RL_WEIGHTS_PATH = ".tmp/rl_weights.json"


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise resources on startup, clean up on shutdown."""
    logger.info("Initialising database …")
    await feedback.init_db()

    # Load RL weights if they exist
    try:
        prioritizer.load(RL_WEIGHTS_PATH)
        logger.info("RL weights loaded from %s", RL_WEIGHTS_PATH)
    except FileNotFoundError:
        logger.info("No existing RL weights — starting fresh.")

    logger.info("✅ Backend ready.")
    yield

    # Save RL weights on shutdown
    try:
        prioritizer.save(RL_WEIGHTS_PATH)
        logger.info("RL weights saved to %s", RL_WEIGHTS_PATH)
    except Exception as e:
        logger.warning("Failed to save RL weights: %s", e)


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AI Code Review Backend",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Health check — used by the extension on activation."""
    return {"status": "healthy", "service": "ai-review-backend"}


@app.post("/review", response_model=ReviewResponse)
async def review_file(req: ReviewRequest):
    """
    Review a single file.
    Runs Semgrep, LLM, and OSV scanners in parallel, then fuses
    and ranks the results.
    """
    t0 = time.time()
    logger.info("POST /review — file=%s (%d bytes)", req.file_path, len(req.content))

    # Run all scanners in parallel
    semgrep_task = scanner_semgrep.scan_file(req.file_path, req.content)
    llm_task = scanner_llm.scan_file(req.file_path, req.content)
    osv_task = scanner_osv.scan_file(req.file_path, req.content)

    semgrep_findings, llm_findings, osv_findings = await asyncio.gather(
        semgrep_task, llm_task, osv_task,
        return_exceptions=True,
    )

    # Collect results, handling per-scanner failures gracefully
    all_findings = []
    sources_used: list[FindingSource] = []

    if isinstance(semgrep_findings, list):
        all_findings.extend(semgrep_findings)
        if semgrep_findings:
            sources_used.append(FindingSource.semgrep)
        logger.info("  Semgrep: %d findings", len(semgrep_findings))
    else:
        logger.warning("  Semgrep scanner failed: %s", semgrep_findings)

    if isinstance(llm_findings, list):
        all_findings.extend(llm_findings)
        if llm_findings:
            sources_used.append(FindingSource.llm)
        logger.info("  LLM: %d findings", len(llm_findings))
    else:
        logger.warning("  LLM scanner failed: %s", llm_findings)

    if isinstance(osv_findings, list):
        all_findings.extend(osv_findings)
        if osv_findings:
            sources_used.append(FindingSource.osv)
        logger.info("  OSV: %d findings", len(osv_findings))
    else:
        logger.warning("  OSV scanner failed: %s", osv_findings)

    # Fuse duplicates
    fused = await fusion.fuse_findings(all_findings)
    if FindingSource.fused not in sources_used and any(
        f.source == FindingSource.fused for f in fused
    ):
        sources_used.append(FindingSource.fused)

    # Apply RL priority adjustments
    for f in fused:
        adj = prioritizer.get_priority_adjustment(f)
        # We don't mutate the finding directly; scoring will read confidence
        # and the prioritizer influences via scoring integration

    # Score and rank
    ranked = scoring.rank_findings(fused)

    # Cache findings for later lookup (explain, fix)
    for f in ranked:
        await feedback.cache_finding(f)

    elapsed_ms = (time.time() - t0) * 1000.0

    logger.info("  Total: %d findings in %.0f ms", len(ranked), elapsed_ms)

    return ReviewResponse(
        file_path=req.file_path,
        findings=ranked,
        scan_duration_ms=round(elapsed_ms, 1),
        sources_used=sources_used,
    )


@app.post("/review/repo", response_model=ReviewResponse)
async def review_repo(req: RepoReviewRequest):
    """
    Review an entire repository.
    Runs Semgrep and OSV at repo level; LLM is skipped for full-repo
    (too expensive — it works per-file in the single-file endpoint).
    """
    t0 = time.time()
    logger.info("POST /review/repo — path=%s", req.repo_path)

    semgrep_task = scanner_semgrep.scan_repo(req.repo_path)
    osv_task = scanner_osv.scan_repo(req.repo_path)

    semgrep_findings, osv_findings = await asyncio.gather(
        semgrep_task, osv_task,
        return_exceptions=True,
    )

    all_findings = []
    sources_used: list[FindingSource] = []

    if isinstance(semgrep_findings, list):
        all_findings.extend(semgrep_findings)
        if semgrep_findings:
            sources_used.append(FindingSource.semgrep)
    else:
        logger.warning("Semgrep repo scan failed: %s", semgrep_findings)

    if isinstance(osv_findings, list):
        all_findings.extend(osv_findings)
        if osv_findings:
            sources_used.append(FindingSource.osv)
    else:
        logger.warning("OSV repo scan failed: %s", osv_findings)

    fused = await fusion.fuse_findings(all_findings)
    ranked = scoring.rank_findings(fused)

    for f in ranked:
        await feedback.cache_finding(f)

    elapsed_ms = (time.time() - t0) * 1000.0

    return ReviewResponse(
        file_path=req.repo_path,
        findings=ranked,
        scan_duration_ms=round(elapsed_ms, 1),
        sources_used=sources_used,
    )


@app.post("/explain", response_model=ExplainResponse)
async def explain_finding(req: ExplainRequest):
    """
    Explain a finding with XAI — generates human-readable explanation
    and token-level attributions.
    """
    logger.info("POST /explain — finding_id=%s", req.finding_id)

    # Retrieve the cached finding
    finding = await feedback.get_cached_finding(req.finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail=f"Finding {req.finding_id} not found in cache.")

    # Use the LLM-based explanation or XAI module
    explanation, attributions, highlighted = await xai.generate_explanation(
        finding,
        code_context=finding.description,  # Ideally we'd have the full file cached
    )

    return ExplainResponse(
        finding_id=req.finding_id,
        explanation=explanation,
        token_attributions=attributions,
        highlighted_lines=highlighted,
    )


@app.post("/fix", response_model=FixResponse)
async def generate_fix(req: FixRequest):
    """
    Generate a code fix suggestion for a finding using the LLM.
    """
    logger.info("POST /fix — finding_id=%s", req.finding_id)

    finding = await feedback.get_cached_finding(req.finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail=f"Finding {req.finding_id} not found in cache.")

    fix_suggestion, confidence = await xai.generate_fix(finding, req.content)

    return FixResponse(
        finding_id=req.finding_id,
        fix_suggestion=fix_suggestion,
        confidence=confidence,
    )


@app.post("/feedback")
async def submit_feedback(event: FeedbackEvent):
    """
    Record developer feedback (accept/reject) on a finding.
    Updates the RL model and persists to SQLite.
    """
    logger.info(
        "POST /feedback — finding_id=%s status=%s",
        event.finding_id,
        event.status,
    )

    # Persist feedback
    await feedback.store_feedback(event)

    # Update RL model
    finding = await feedback.get_cached_finding(event.finding_id)
    if finding:
        reward = 1.0 if event.status == "accepted" else -1.0
        prioritizer.update(finding, reward)

    return {"status": "recorded", "finding_id": event.finding_id}


@app.post("/voice")
async def process_voice_command(cmd: VoiceCommand):
    """
    Receive voice command from the extension.
    Currently logs for metrics; the extension handles command execution locally.
    """
    logger.info(
        "POST /voice — command=%s transcript='%s'",
        cmd.command,
        cmd.transcript[:80],
    )
    return {"status": "ok", "command": cmd.command}


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    import os

    host = os.getenv("BACKEND_HOST", "0.0.0.0")
    port = int(os.getenv("BACKEND_PORT", "8000"))

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=True,
        log_level="info",
    )
