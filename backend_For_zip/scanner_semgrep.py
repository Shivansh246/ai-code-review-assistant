import asyncio
import json
import logging
import os
import sys
import tempfile
import uuid
from typing import List

from models import Finding, FindingSource, Severity

logger = logging.getLogger(__name__)

SEMGREP_TIMEOUT = float(os.environ.get("SEMGREP_TIMEOUT", "30.0"))

def _get_semgrep_bin() -> str:
    """Return configured SEMGREP_PATH or auto-detect semgrep in virtual environment."""
    path_env = os.environ.get("SEMGREP_PATH")
    if path_env:
        return path_env
    venv_bin = os.path.join(os.path.dirname(sys.executable), "semgrep")
    if os.path.exists(venv_bin):
        return venv_bin
    return "semgrep"

def _map_severity(semgrep_severity: str) -> Severity:
    """Map Semgrep severity to our Severity model."""
    semgrep_severity = semgrep_severity.upper()
    if semgrep_severity == "ERROR":
        return Severity.high
    elif semgrep_severity == "WARNING":
        return Severity.medium
    else:
        return Severity.low

def _parse_semgrep_output(output: str, original_path_override: str = None) -> List[Finding]:
    """Parse JSON output from Semgrep into a list of Finding objects."""
    findings = []
    if not output.strip():
        return findings
    try:
        data = json.loads(output)
        results = data.get("results", [])
        for res in results:
            extra = res.get("extra", {})
            metadata = extra.get("metadata", {})
            confidence = metadata.get("confidence")
            
            # Semgrep sometimes uses HIGH/MEDIUM/LOW for confidence
            if isinstance(confidence, (int, float)):
                conf_val = float(confidence)
            elif isinstance(confidence, str):
                conf_upper = confidence.upper()
                if conf_upper == "HIGH":
                    conf_val = 0.9
                elif conf_upper == "MEDIUM":
                    conf_val = 0.6
                elif conf_upper == "LOW":
                    conf_val = 0.3
                else:
                    conf_val = 0.8
            else:
                conf_val = 0.8

            start = res.get("start", {})
            end = res.get("end", {})

            path = original_path_override if original_path_override else res.get("path", "")
            rule_id = res.get("check_id", "unknown")
            message = extra.get("message", "No message provided")

            finding = Finding(
                id=str(uuid.uuid4()),
                source=FindingSource.semgrep,
                rule_id=rule_id,
                title=rule_id,
                description=message,
                severity=_map_severity(extra.get("severity", "INFO")),
                file=path,
                line_start=start.get("line", 1),
                line_end=end.get("line", start.get("line", 1)),
                column_start=start.get("col", 1),
                column_end=end.get("col", start.get("col", 1)),
                confidence=conf_val
            )
            findings.append(finding)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse semgrep JSON output: {e}")
    except Exception as e:
        logger.error(f"Error processing semgrep output: {e}")
    
    return findings

async def _run_semgrep(target_path: str) -> str:
    """Run semgrep CLI as a subprocess with timeout and return the JSON output."""
    semgrep_bin = _get_semgrep_bin()
    try:
        process = await asyncio.create_subprocess_exec(
            semgrep_bin,
            "--json",
            "--config", "auto",
            target_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=SEMGREP_TIMEOUT
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Semgrep timed out after {SEMGREP_TIMEOUT}s on {target_path}. Terminating subprocess."
            )
            try:
                process.kill()
                await process.wait()
            except Exception as kill_err:
                logger.error(f"Error terminating timed-out semgrep process: {kill_err}")
            return ""

        # Semgrep exits with 1 if it finds issues. If it exits with non-zero and no stdout, it failed.
        if process.returncode != 0 and not stdout.strip():
            logger.warning(f"Semgrep failed with return code {process.returncode}. Stderr: {stderr.decode()}")
            
        return stdout.decode()
    except FileNotFoundError:
        logger.warning(f"Semgrep binary not found at '{semgrep_bin}'. Ensure it is installed or set SEMGREP_PATH.")
        return ""
    except Exception as e:
        logger.error(f"Error running semgrep: {e}")
        return ""

async def scan_file(file_path: str, content: str) -> List[Finding]:
    """
    Writes content to a temp file, runs semgrep on it, and returns findings.
    """
    _, ext = os.path.splitext(file_path)
    suffix = ext if ext else ".tmp"
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    
    try:
        output = await _run_semgrep(tmp_path)
        return _parse_semgrep_output(output, original_path_override=file_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

async def scan_repo(repo_path: str) -> List[Finding]:
    """
    Runs semgrep on the whole directory and returns findings.
    """
    if not os.path.isdir(repo_path):
        logger.error(f"Repository path does not exist: {repo_path}")
        return []
    output = await _run_semgrep(repo_path)
    return _parse_semgrep_output(output)
