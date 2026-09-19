"""
test_regression.py
Regression tests for backend stabilization fixes:
1. Semgrep finding construction with canonical fields
2. OSV finding construction with canonical fields
3. Semgrep subprocess timeout handling
4. Non-blocking async execution of LLM provider
5. Fusion finding construction and duplicate matching with canonical fields
"""

import asyncio
import json
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from models import Finding, FindingSource, Severity
import fusion
import scanner_llm
import scanner_osv
import scanner_semgrep


class TestSemgrepRegression(unittest.TestCase):
    """Tests for Semgrep parser and subprocess timeout."""

    def test_semgrep_parser_canonical_fields(self):
        sample_json = json.dumps({
            "results": [
                {
                    "check_id": "rules.python.security.sqli",
                    "path": "temp_scan.py",
                    "start": {"line": 15, "col": 5},
                    "end": {"line": 18, "col": 30},
                    "extra": {
                        "message": "Possible SQL injection detected in raw query formatting.",
                        "severity": "ERROR",
                        "metadata": {"confidence": "HIGH"}
                    }
                }
            ]
        })

        findings = scanner_semgrep._parse_semgrep_output(sample_json, original_path_override="src/database.py")
        self.assertEqual(len(findings), 1)

        f = findings[0]
        # Verify all canonical fields
        self.assertEqual(f.file, "src/database.py")
        self.assertEqual(f.rule_id, "rules.python.security.sqli")
        self.assertEqual(f.title, "rules.python.security.sqli")
        self.assertEqual(f.description, "Possible SQL injection detected in raw query formatting.")
        self.assertEqual(f.line_start, 15)
        self.assertEqual(f.line_end, 18)
        self.assertEqual(f.column_start, 5)
        self.assertEqual(f.column_end, 30)
        self.assertEqual(f.severity, Severity.high)
        self.assertEqual(f.source, FindingSource.semgrep)
        self.assertAlmostEqual(f.confidence, 0.9)

    def test_semgrep_timeout_handling(self):
        async def run_timeout_test():
            mock_proc = AsyncMock()

            # communicate() simulates hanging indefinitely
            async def slow_communicate():
                await asyncio.sleep(10.0)
                return (b"stdout", b"stderr")

            mock_proc.communicate = slow_communicate
            mock_proc.kill = MagicMock()
            mock_proc.wait = AsyncMock()

            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                with patch.object(scanner_semgrep, "SEMGREP_TIMEOUT", 0.05):
                    output = await scanner_semgrep._run_semgrep("target_dir")
                    self.assertEqual(output, "")
                    mock_proc.kill.assert_called_once()

        asyncio.run(run_timeout_test())


class TestOSVRegression(unittest.TestCase):
    """Tests for OSV manifest parser and vulnerability finding construction."""

    def test_osv_parser_canonical_fields(self):
        fake_osv_response = {
            "results": [
                {
                    "vulns": [
                        {
                            "id": "GHSA-w749-p3v6-hccq",
                            "summary": "Jinja2 sandbox breakout vulnerability via string formatting",
                            "details": "Extended details about the sandbox breakout...",
                            "database_specific": {"severity": "HIGH"}
                        }
                    ]
                }
            ]
        }

        async def run_osv_test():
            with patch("httpx.AsyncClient.post") as mock_post:
                mock_resp = AsyncMock()
                mock_resp.status_code = 200
                mock_resp.raise_for_status = lambda: None
                mock_resp.json = lambda: fake_osv_response
                mock_post.return_value = mock_resp

                manifest_content = "jinja2==2.10.1\nrequests==2.20.0"
                findings = await scanner_osv.scan_file("requirements.txt", manifest_content)

                self.assertEqual(len(findings), 1)
                f = findings[0]

                # Verify all canonical fields
                self.assertEqual(f.file, "requirements.txt")
                self.assertEqual(f.package_name, "jinja2")
                self.assertEqual(f.package_version, "2.10.1")
                self.assertEqual(f.cve_id, "GHSA-w749-p3v6-hccq")
                self.assertEqual(f.title, "Vulnerability in jinja2 (GHSA-w749-p3v6-hccq)")
                self.assertEqual(f.description, "Jinja2 sandbox breakout vulnerability via string formatting")
                self.assertEqual(f.source, FindingSource.osv)
                self.assertEqual(f.severity, Severity.high)
                self.assertEqual(f.line_start, 1)
                self.assertEqual(f.line_end, 1)
                self.assertEqual(f.confidence, 1.0)

        asyncio.run(run_osv_test())


class TestLLMAsyncRegression(unittest.TestCase):
    """Tests for LLM non-blocking async execution and finding construction."""

    def test_llm_async_non_blocking(self):
        async def run_async_test():
            # Mock Gemini client where generate_content does a synchronous sleep
            mock_client = MagicMock()
            # Simulate a client without aio (or using fallback to_thread)
            del mock_client.aio

            def blocking_generate_content(*args, **kwargs):
                time.sleep(0.1)
                resp = MagicMock()
                resp.text = json.dumps([
                    {
                        "title": "Hardcoded AWS Credentials",
                        "description": "Found hardcoded AWS_SECRET_ACCESS_KEY.",
                        "severity": "critical",
                        "line_start": 10,
                        "line_end": 10,
                        "confidence": 0.99,
                        "rule_id": "llm-hardcoded-secret"
                    }
                ])
                return resp

            mock_client.models.generate_content = blocking_generate_content

            with patch.dict("os.environ", {"GEMINI_API_KEY": "dummy-key", "LLM_PROVIDER": "gemini"}):
                with patch("scanner_llm.genai") as mock_genai:
                    mock_genai.Client.return_value = mock_client

                    # Run background task on the same event loop to ensure it's not blocked
                    heartbeat_ticks = 0

                    async def heartbeat():
                        nonlocal heartbeat_ticks
                        for _ in range(5):
                            await asyncio.sleep(0.02)
                            heartbeat_ticks += 1

                    scan_task = asyncio.create_task(scanner_llm.scan_file("config.py", "AWS_SECRET='123'"))
                    heartbeat_task = asyncio.create_task(heartbeat())

                    findings, _ = await asyncio.gather(scan_task, heartbeat_task)

                    # Heartbeat must have ticked while LLM call was running in thread
                    self.assertGreater(heartbeat_ticks, 0, "Event loop was blocked by synchronous LLM call!")

                    self.assertEqual(len(findings), 1)
                    f = findings[0]
                    self.assertEqual(f.file, "config.py")
                    self.assertEqual(f.title, "Hardcoded AWS Credentials")
                    self.assertEqual(f.description, "Found hardcoded AWS_SECRET_ACCESS_KEY.")
                    self.assertEqual(f.severity, Severity.critical)
                    self.assertEqual(f.source, FindingSource.llm)
                    self.assertEqual(f.rule_id, "llm-hardcoded-secret")

        asyncio.run(run_async_test())


class TestFusionRegression(unittest.TestCase):
    """Tests for Finding deduplication and fusion using canonical fields."""

    def test_fusion_canonical_fields(self):
        async def run_fusion_test():
            f1 = Finding(
                source=FindingSource.semgrep,
                severity=Severity.medium,
                title="SQL Injection",
                description="Semgrep detected SQL query concatenation.",
                file="app/main.py",
                line_start=20,
                line_end=25,
                confidence=0.8,
                rule_id="sec-sql-01"
            )
            f2 = Finding(
                source=FindingSource.llm,
                severity=Severity.high,
                title="SQL Injection Vulnerability",
                description="LLM detected unescaped SQL parameter in query.",
                file="app/main.py",
                line_start=21,
                line_end=24,
                confidence=0.9,
                rule_id="sec-sql-01"
            )

            fused = await fusion.fuse_findings([f1, f2])
            self.assertEqual(len(fused), 1)

            mf = fused[0]
            self.assertEqual(mf.file, "app/main.py")
            self.assertEqual(mf.source, FindingSource.fused)
            self.assertEqual(mf.severity, Severity.high)
            self.assertGreater(mf.confidence, 0.9)
            self.assertEqual(mf.line_start, 21)
            self.assertEqual(mf.line_end, 24)
            self.assertIn("Semgrep detected", mf.description)
            self.assertIn("LLM detected", mf.description)

        asyncio.run(run_fusion_test())


if __name__ == "__main__":
    unittest.main()
