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
import os
import time
import uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

from models import (
    ExplainRequest,
    ExplainResponse,
    FeedbackEvent,
    FeedbackStatus,
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
import vosk_service
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
rl_lock = asyncio.Lock()


def is_rl_enabled() -> bool:
    """Returns True only when USE_RL_PRIORITIZATION environment variable is set to 1."""
    return os.getenv("USE_RL_PRIORITIZATION", "0").strip().lower() in ("1", "true", "yes")


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise resources on startup, clean up on shutdown."""
    logger.info("Initialising database …")
    await feedback.init_db()

    # Load RL weights only if RL is enabled
    if is_rl_enabled():
        try:
            prioritizer.load(RL_WEIGHTS_PATH)
            logger.info("RL weights loaded from %s", RL_WEIGHTS_PATH)
        except Exception as e:
            logger.warning("Could not load RL weights (starting fresh): %s", e)
    else:
        logger.info("RL prioritization disabled by default (USE_RL_PRIORITIZATION=0).")

    logger.info("✅ Backend ready.")
    yield

    # Save RL weights on shutdown only if RL is enabled and model was trained
    if is_rl_enabled():
        try:
            if prioritizer.update_count > 0:
                prioritizer.save(RL_WEIGHTS_PATH)
                logger.info("RL weights saved to %s", RL_WEIGHTS_PATH)
            else:
                logger.info("RL weights not modified; skipping save on shutdown.")
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

    request_id = str(uuid.uuid4())
    applied_actions: dict[str, float] = {}

    if is_rl_enabled():
        try:
            ranked, applied_actions = scoring.rank_findings_rl(fused, prioritizer, review_id=request_id)
            logger.info("Applied opt-in RL prioritization to %d findings", len(ranked))
        except Exception as e:
            logger.warning("RL prioritization failed, falling back to Phase 6A adaptive scoring: %s", e)
            applied_actions.clear()
            try:
                fb_stats = await feedback.get_feedback_stats()
                ranked = scoring.rank_findings_adaptive(fused, fb_stats)
            except Exception as fb_err:
                logger.warning("Failed to retrieve feedback stats, falling back to static scoring: %s", fb_err)
                ranked = scoring.rank_findings(fused)
    else:
        # Phase 6A deterministic adaptive baseline (default)
        try:
            fb_stats = await feedback.get_feedback_stats()
            ranked = scoring.rank_findings_adaptive(fused, fb_stats)
        except Exception as e:
            logger.warning("Failed to retrieve feedback stats, falling back to static scoring: %s", e)
            ranked = scoring.rank_findings(fused)

    # Cache findings for later lookup (explain, fix) and persist applied RL action if enabled
    for f in ranked:
        f.review_id = request_id
        await feedback.cache_finding(f)
        if is_rl_enabled() and f.id in applied_actions:
            await feedback.record_rl_action(f.id, applied_actions[f.id], review_id=request_id)

    elapsed_ms = (time.time() - t0) * 1000.0

    logger.info("  Total: %d findings in %.0f ms", len(ranked), elapsed_ms)

    return ReviewResponse(
        request_id=request_id,
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
    request_id = str(uuid.uuid4())
    applied_actions: dict[str, float] = {}

    if is_rl_enabled():
        try:
            ranked, applied_actions = scoring.rank_findings_rl(fused, prioritizer, review_id=request_id)
            logger.info("Applied opt-in RL prioritization to %d repo findings", len(ranked))
        except Exception as e:
            logger.warning("RL prioritization failed, falling back to Phase 6A adaptive scoring: %s", e)
            applied_actions.clear()
            try:
                fb_stats = await feedback.get_feedback_stats()
                ranked = scoring.rank_findings_adaptive(fused, fb_stats)
            except Exception as fb_err:
                logger.warning("Failed to retrieve feedback stats, falling back to static scoring: %s", fb_err)
                ranked = scoring.rank_findings(fused)
    else:
        try:
            fb_stats = await feedback.get_feedback_stats()
            ranked = scoring.rank_findings_adaptive(fused, fb_stats)
        except Exception as e:
            logger.warning("Failed to retrieve feedback stats, falling back to static scoring: %s", e)
            ranked = scoring.rank_findings(fused)

    for f in ranked:
        f.review_id = request_id
        await feedback.cache_finding(f)
        if is_rl_enabled() and f.id in applied_actions:
            await feedback.record_rl_action(f.id, applied_actions[f.id], review_id=request_id)

    elapsed_ms = (time.time() - t0) * 1000.0

    return ReviewResponse(
        request_id=request_id,
        file_path=req.repo_path,
        findings=ranked,
        scan_duration_ms=round(elapsed_ms, 1),
        sources_used=sources_used,
    )


@app.post("/explain", response_model=ExplainResponse)
async def explain_finding(req: ExplainRequest):
    """
    Explain a finding with XAI — generates human-readable explanation,
    evidence snippet, risk score breakdown, and token attributions.
    """
    logger.info("POST /explain — finding_id=%s", req.finding_id)

    # Retrieve the cached finding
    finding = await feedback.get_cached_finding(req.finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail=f"Finding {req.finding_id} not found in cache.")

    res = await xai.generate_structured_explanation(finding)

    return ExplainResponse(
        finding_id=req.finding_id,
        explanation=res["explanation"],
        token_attributions=res.get("token_attributions", []),
        highlighted_lines=res.get("highlighted_lines", []),
        rule_id=res.get("rule_id"),
        severity=res.get("severity"),
        risk_score=res.get("risk_score"),
        score_factors=res.get("score_factors"),
        evidence_snippet=res.get("evidence_snippet"),
        remediation_rationale=res.get("remediation_rationale"),
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

    # 1. Always persist feedback (Phase 6A baseline)
    await feedback.store_feedback(event)

    # 2. Update RL model only when opt-in enabled
    if is_rl_enabled():
        try:
            async with rl_lock:
                # The server's SQLite record is authoritative
                persisted_action = await feedback.get_and_consume_rl_action(event.finding_id, event.review_id)
                if persisted_action is not None:
                    if event.applied_action is not None and event.applied_action != persisted_action:
                        logger.warning(
                            "Client-supplied action %.2f differs from authoritative server action %.2f for finding %s; using server action",
                            event.applied_action,
                            persisted_action,
                            event.finding_id,
                        )
                    action = persisted_action
                    cached_finding = await feedback.get_cached_finding(event.finding_id)
                    if cached_finding:
                        reward = 1.0 if (event.status == FeedbackStatus.accepted or event.status == "accepted") else -1.0
                        prioritizer.update(cached_finding, reward, action=action)
                        logger.info(
                            "Updated RL policy for finding %s: action=%.2f, reward=%.1f",
                            event.finding_id,
                            action,
                            reward,
                        )
                    else:
                        logger.warning("Finding %s not found in cache for RL update", event.finding_id)
                else:
                    logger.info("No unconsumed RL action found for finding %s; skipping RL update", event.finding_id)
        except Exception as e:
            logger.warning("RL feedback update failed gracefully: %s", e)

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


@app.post("/voice/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """
    Upload recorded audio file for speech-to-text transcription using local, offline Vosk.
    Zero cloud API calls, zero cost, completely local execution.
    Input constraints:
    - Maximum audio file size: 10MB
    - Content-Type / extension: wav, mp3, ogg, webm, flac, m4a
    """
    MAX_AUDIO_SIZE = 10 * 1024 * 1024  # 10 MB
    ALLOWED_EXTENSIONS = {".wav", ".mp3", ".ogg", ".webm", ".flac", ".m4a"}

    filename = file.filename or "audio.wav"
    _, ext = os.path.splitext(filename.lower())
    if ext not in ALLOWED_EXTENSIONS and not (file.content_type and "audio" in file.content_type):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio format '{ext}'. Allowed formats: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    # Read uploaded content and validate size
    content = await file.read()
    if not content or len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded audio file is empty.")
    if len(content) > MAX_AUDIO_SIZE:
        raise HTTPException(status_code=413, detail="Audio file exceeds maximum size limit (10MB).")

    # Local Vosk Speech-to-Text Transcription
    try:
        # Run blocking Kaldi recognition in default threadpool to keep async event loop responsive
        loop = asyncio.get_running_loop()
        transcript = await loop.run_in_executor(
            None,
            vosk_service.transcribe_audio_bytes,
            content,
            filename,
        )
        logger.info("Local Vosk recognized transcript: '%s'", transcript[:60])
        return {"transcript": transcript}
    except FileNotFoundError as e:
        logger.error("Vosk model missing: %s", e)
        raise HTTPException(
            status_code=503,
            detail=str(e),
        )
    except RuntimeError as e:
        logger.error("Vosk runtime error: %s", e)
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )
    except ValueError as e:
        logger.warning("Vosk audio decode error: %s", e)
        raise HTTPException(
            status_code=400,
            detail=f"Invalid audio format: {e}",
        )
    except Exception as e:
        logger.error("Unexpected error in local Vosk transcription: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Internal local speech recognition error: {e}",
        )




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
