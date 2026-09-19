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

    def test_semgrep_empty_results(self):
        sample_json = json.dumps({"results": []})
        findings = scanner_semgrep._parse_semgrep_output(sample_json, original_path_override="src/clean.py")
        self.assertEqual(findings, [])

    def test_semgrep_malformed_json(self):
        malformed_json = "{'results': [invalid_json}"
        findings = scanner_semgrep._parse_semgrep_output(malformed_json, original_path_override="src/clean.py")
        self.assertEqual(findings, [])

    def test_semgrep_nonzero_exit_code(self):
        async def run_nonzero_test():
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b"Fatal error reading config"))
            mock_proc.returncode = 2

            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                output = await scanner_semgrep._run_semgrep("target_dir")
                self.assertEqual(output, "")

        asyncio.run(run_nonzero_test())

    def test_semgrep_scoring_compatibility(self):
        sample_json = json.dumps({
            "results": [
                {
                    "check_id": "rules.python.security.sqli",
                    "path": "app.py",
                    "start": {"line": 10, "col": 1},
                    "end": {"line": 12, "col": 20},
                    "extra": {
                        "message": "SQL Injection",
                        "severity": "ERROR",
                        "metadata": {"confidence": "HIGH"}
                    }
                },
                {
                    "check_id": "rules.python.security.info",
                    "path": "app.py",
                    "start": {"line": 1, "col": 1},
                    "end": {"line": 1, "col": 10},
                    "extra": {
                        "message": "Info note",
                        "severity": "INFO",
                        "metadata": {"confidence": "LOW"}
                    }
                }
            ]
        })
        findings = scanner_semgrep._parse_semgrep_output(sample_json)
        self.assertEqual(len(findings), 2)

        import scoring
        ranked = scoring.rank_findings(findings)
        self.assertEqual(len(ranked), 2)
        self.assertEqual(ranked[0].severity, Severity.high)
        self.assertEqual(ranked[1].severity, Severity.low)

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

    def test_dependency_parsers(self):
        # Python requirements.txt
        req_deps = scanner_osv._parse_requirements_txt("jinja2==2.10.1\n  requests[security] == 2.20.0\n# comment\n")
        self.assertEqual(len(req_deps), 2)
        self.assertEqual(req_deps[0]["package"]["name"], "jinja2")
        self.assertEqual(req_deps[0]["version"], "2.10.1")
        self.assertEqual(req_deps[1]["package"]["name"], "requests")
        self.assertEqual(req_deps[1]["version"], "2.20.0")

        # npm package.json
        pkg_json = json.dumps({
            "dependencies": {"lodash": "^4.17.11"},
            "devDependencies": {"mocha": "~8.0.0"}
        })
        npm_deps = scanner_osv._parse_package_json(pkg_json)
        self.assertEqual(len(npm_deps), 2)
        self.assertEqual(npm_deps[0]["package"]["name"], "lodash")
        self.assertEqual(npm_deps[0]["version"], "4.17.11")

        # Go go.mod
        go_mod = "module example.com/app\nrequire (\n\tgithub.com/gin-gonic/gin v1.6.0\n)\n"
        go_deps = scanner_osv._parse_go_mod(go_mod)
        self.assertEqual(len(go_deps), 1)
        self.assertEqual(go_deps[0]["package"]["name"], "github.com/gin-gonic/gin")
        self.assertEqual(go_deps[0]["version"], "v1.6.0")

        # Maven pom.xml
        pom_xml = """<project><dependencies><dependency>
            <groupId>org.apache.commons</groupId>
            <artifactId>commons-text</artifactId>
            <version>1.9</version>
        </dependency></dependencies></project>"""
        pom_deps = scanner_osv._parse_pom_xml(pom_xml)
        self.assertEqual(len(pom_deps), 1)
        self.assertEqual(pom_deps[0]["package"]["name"], "org.apache.commons:commons-text")
        self.assertEqual(pom_deps[0]["version"], "1.9")

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

    def test_osv_no_vulnerabilities(self):
        fake_osv_response = {"results": [{"vulns": []}]}

        async def run_test():
            with patch("httpx.AsyncClient.post") as mock_post:
                mock_resp = AsyncMock()
                mock_resp.status_code = 200
                mock_resp.raise_for_status = lambda: None
                mock_resp.json = lambda: fake_osv_response
                mock_post.return_value = mock_resp

                findings = await scanner_osv.scan_file("requirements.txt", "safe-pkg==1.0.0")
                self.assertEqual(findings, [])

        asyncio.run(run_test())

    def test_osv_malformed_dependency_input(self):
        async def run_test():
            # Malformed JSON for package.json
            findings_pkg = await scanner_osv.scan_file("package.json", "{invalid_json")
            self.assertEqual(findings_pkg, [])

            # Malformed XML for pom.xml
            findings_pom = await scanner_osv.scan_file("pom.xml", "<unclosed_tag>")
            self.assertEqual(findings_pom, [])

            # Unsupported file
            findings_other = await scanner_osv.scan_file("script.py", "import os")
            self.assertEqual(findings_other, [])

        asyncio.run(run_test())

    def test_osv_malformed_api_response(self):
        async def run_test():
            with patch("httpx.AsyncClient.post") as mock_post:
                mock_resp = AsyncMock()
                mock_resp.status_code = 200
                mock_resp.raise_for_status = lambda: None
                mock_resp.json = lambda: {"results": "invalid_results_type"}
                mock_post.return_value = mock_resp

                findings = await scanner_osv.scan_file("requirements.txt", "jinja2==2.10.1")
                self.assertEqual(findings, [])

        asyncio.run(run_test())

    def test_osv_network_failure_and_timeout(self):
        async def run_test():
            import httpx
            with patch("httpx.AsyncClient.post", side_effect=httpx.TimeoutException("OSV timeout")):
                findings_timeout = await scanner_osv.scan_file("requirements.txt", "jinja2==2.10.1")
                self.assertEqual(findings_timeout, [])

            with patch("httpx.AsyncClient.post", side_effect=httpx.RequestError("Connection refused")):
                findings_err = await scanner_osv.scan_file("requirements.txt", "jinja2==2.10.1")
                self.assertEqual(findings_err, [])

        asyncio.run(run_test())

    def test_osv_scoring_compatibility(self):
        async def run_test():
            fake_osv_response = {
                "results": [
                    {
                        "vulns": [
                            {
                                "id": "GHSA-1234-5678",
                                "summary": "High vuln in pkg-a",
                                "database_specific": {"severity": "HIGH"}
                            },
                            {
                                "id": "GHSA-8765-4321",
                                "summary": "Low vuln in pkg-a",
                                "database_specific": {"severity": "LOW"}
                            }
                        ]
                    }
                ]
            }
            with patch("httpx.AsyncClient.post") as mock_post:
                mock_resp = AsyncMock()
                mock_resp.status_code = 200
                mock_resp.raise_for_status = lambda: None
                mock_resp.json = lambda: fake_osv_response
                mock_post.return_value = mock_resp

                findings = await scanner_osv.scan_file("requirements.txt", "pkg-a==1.0.0")
                self.assertEqual(len(findings), 2)

                import scoring
                ranked = scoring.rank_findings(findings)
                self.assertEqual(len(ranked), 2)
                self.assertEqual(ranked[0].severity, Severity.high)
                self.assertEqual(ranked[1].severity, Severity.low)
                self.assertGreater(
                    scoring.finding_risk_scores[ranked[0].id],
                    scoring.finding_risk_scores[ranked[1].id]
                )

        asyncio.run(run_test())


class TestLLMAsyncRegression(unittest.TestCase):
    """Tests for LLM non-blocking async execution, error handling, and normalization."""

    def test_llm_valid_single_finding(self):
        """Test 1: Valid structured LLM response -> Finding."""
        async def run_test():
            mock_resp = MagicMock()
            mock_resp.text = json.dumps([
                {
                    "title": "SQL Injection",
                    "description": "User input passed to raw SQL query.",
                    "severity": "high",
                    "line_start": 12,
                    "line_end": 15,
                    "confidence": 0.9,
                    "rule_id": "llm-sqli"
                }
            ])
            with patch.dict("os.environ", {"GEMINI_API_KEY": "dummy", "LLM_PROVIDER": "gemini"}):
                with patch("scanner_llm._call_llm", AsyncMock(return_value=mock_resp.text)):
                    findings = await scanner_llm.scan_file("app.py", "query = f'SELECT * FROM users WHERE id={user_id}'\n" * 20)
                    self.assertEqual(len(findings), 1)
                    f = findings[0]
                    self.assertEqual(f.file, "app.py")
                    self.assertEqual(f.title, "SQL Injection")
                    self.assertEqual(f.severity, Severity.high)
                    self.assertEqual(f.source, FindingSource.llm)
                    self.assertEqual(f.line_start, 12)
                    self.assertEqual(f.line_end, 15)
                    self.assertAlmostEqual(f.confidence, 0.9)
                    self.assertEqual(f.rule_id, "llm-sqli")
        asyncio.run(run_test())

    def test_llm_multiple_findings(self):
        """Test 2: Multiple findings -> multiple canonical Findings."""
        async def run_test():
            mock_resp = json.dumps([
                {
                    "title": "Issue 1",
                    "description": "Desc 1",
                    "severity": "critical",
                    "line_start": 2,
                    "line_end": 3,
                    "confidence": 0.95,
                    "rule_id": "rule-1"
                },
                {
                    "title": "Issue 2",
                    "description": "Desc 2",
                    "severity": "low",
                    "line_start": 5,
                    "line_end": 5,
                    "confidence": 0.7,
                    "rule_id": "rule-2"
                }
            ])
            with patch.dict("os.environ", {"GEMINI_API_KEY": "dummy", "LLM_PROVIDER": "gemini"}):
                with patch("scanner_llm._call_llm", AsyncMock(return_value=mock_resp)):
                    findings = await scanner_llm.scan_file("app.py", "line\n" * 10)
                    self.assertEqual(len(findings), 2)
                    self.assertEqual(findings[0].severity, Severity.critical)
                    self.assertEqual(findings[1].severity, Severity.low)
        asyncio.run(run_test())

    def test_llm_malformed_json(self):
        """Test 3: Malformed JSON -> graceful empty/fallback result."""
        async def run_test():
            with patch.dict("os.environ", {"GEMINI_API_KEY": "dummy"}):
                with patch("scanner_llm._call_llm", AsyncMock(return_value="{invalid_json...")):
                    findings = await scanner_llm.scan_file("app.py", "code")
                    self.assertEqual(findings, [])
        asyncio.run(run_test())

    def test_llm_missing_required_fields(self):
        """Test 4: Missing required fields -> validation/fallback."""
        async def run_test():
            mock_resp = json.dumps([{"severity": "high"}])
            with patch.dict("os.environ", {"GEMINI_API_KEY": "dummy"}):
                with patch("scanner_llm._call_llm", AsyncMock(return_value=mock_resp)):
                    findings = await scanner_llm.scan_file("app.py", "code line\n" * 5)
                    self.assertEqual(len(findings), 1)
                    f = findings[0]
                    self.assertEqual(f.title, "LLM Semantic Security Finding")
                    self.assertEqual(f.line_start, 1)
                    self.assertEqual(f.confidence, 0.8)
        asyncio.run(run_test())

    def test_llm_invalid_severity_and_lines(self):
        """Test 5: Invalid severity & line numbers -> normalization/clamping."""
        async def run_test():
            mock_resp = json.dumps([{
                "title": "Title",
                "description": "Desc",
                "severity": "UNKNOWN_SEVERITY",
                "line_start": 9999,
                "line_end": -5,
                "confidence": 1.5
            }])
            with patch.dict("os.environ", {"GEMINI_API_KEY": "dummy"}):
                with patch("scanner_llm._call_llm", AsyncMock(return_value=mock_resp)):
                    findings = await scanner_llm.scan_file("app.py", "line 1\nline 2\nline 3\n")
                    self.assertEqual(len(findings), 1)
                    f = findings[0]
                    self.assertEqual(f.severity, Severity.info)
                    self.assertEqual(f.line_start, 3) # clamped to total_lines (3)
                    self.assertEqual(f.line_end, 3)
                    self.assertEqual(f.confidence, 1.0) # clamped to 1.0
        asyncio.run(run_test())

    def test_llm_missing_api_key(self):
        """Test 6: Missing API key -> graceful fallback."""
        async def run_test():
            with patch.dict("os.environ", {"GEMINI_API_KEY": "", "OPENAI_API_KEY": ""}, clear=True):
                findings = await scanner_llm.scan_file("app.py", "code")
                self.assertEqual(findings, [])
        asyncio.run(run_test())

    def test_llm_provider_timeout(self):
        """Test 7: Provider timeout -> graceful fallback."""
        async def run_test():
            async def timeout_call(prompt):
                raise asyncio.TimeoutError("LLM API timed out")
            with patch.dict("os.environ", {"GEMINI_API_KEY": "dummy"}):
                with patch("scanner_llm._call_llm", side_effect=timeout_call):
                    findings = await scanner_llm.scan_file("app.py", "code")
                    self.assertEqual(findings, [])
        asyncio.run(run_test())

    def test_llm_provider_network_error(self):
        """Test 8: Provider network error -> graceful fallback."""
        async def run_test():
            with patch.dict("os.environ", {"GEMINI_API_KEY": "dummy"}):
                with patch("scanner_llm._call_llm", side_effect=Exception("Connection reset by peer")):
                    findings = await scanner_llm.scan_file("app.py", "code")
                    self.assertEqual(findings, [])
        asyncio.run(run_test())

    def test_llm_scoring_compatibility(self):
        """Test 11: LLM finding -> existing scoring compatibility."""
        async def run_test():
            f1 = Finding(
                title="Critical Secret Exposure",
                description="Hardcoded key in source.",
                severity=Severity.critical,
                line_start=1,
                line_end=1,
                confidence=0.95,
                source=FindingSource.llm,
                file="app.py"
            )
            f2 = Finding(
                title="Info style note",
                description="Consider updating comments.",
                severity=Severity.info,
                line_start=5,
                line_end=5,
                confidence=0.6,
                source=FindingSource.llm,
                file="app.py"
            )
            import scoring
            ranked = scoring.rank_findings([f1, f2])
            self.assertEqual(len(ranked), 2)
            self.assertEqual(ranked[0].severity, Severity.critical)
            self.assertEqual(ranked[1].severity, Severity.info)
        asyncio.run(run_test())

    def test_llm_async_non_blocking(self):
        """Test 9: Async non-blocking execution."""
        async def run_async_test():
            mock_client = MagicMock()
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

                    heartbeat_ticks = 0

                    async def heartbeat():
                        nonlocal heartbeat_ticks
                        for _ in range(5):
                            await asyncio.sleep(0.02)
                            heartbeat_ticks += 1

                    scan_task = asyncio.create_task(scanner_llm.scan_file("config.py", "AWS_SECRET='123'\n" * 15))
                    heartbeat_task = asyncio.create_task(heartbeat())

                    findings, _ = await asyncio.gather(scan_task, heartbeat_task)

                    self.assertGreater(heartbeat_ticks, 0, "Event loop was blocked by synchronous LLM call!")
                    self.assertEqual(len(findings), 1)
                    f = findings[0]
                    self.assertEqual(f.file, "config.py")
                    self.assertEqual(f.title, "Hardcoded AWS Credentials")
                    self.assertEqual(f.severity, Severity.critical)
                    self.assertEqual(f.source, FindingSource.llm)

        asyncio.run(run_async_test())


class TestFusionRegression(unittest.TestCase):
    """Tests for Finding deduplication and fusion using canonical fields."""

    def test_case1_exact_duplicate(self):
        """Case 1: Exact duplicate -> 1 fused finding."""
        async def run_test():
            f1 = Finding(
                source=FindingSource.semgrep,
                severity=Severity.high,
                title="Use of MD5 hash",
                description="Insecure hash algorithm MD5 used.",
                file="app.py",
                line_start=10,
                line_end=10,
                confidence=0.8,
                rule_id="python.lang.security.insecure-hash.md5"
            )
            f2 = Finding(
                source=FindingSource.semgrep,
                severity=Severity.high,
                title="Use of MD5 hash",
                description="Insecure hash algorithm MD5 used.",
                file="app.py",
                line_start=10,
                line_end=10,
                confidence=0.8,
                rule_id="python.lang.security.insecure-hash.md5"
            )
            fused = await fusion.fuse_findings([f1, f2])
            self.assertEqual(len(fused), 1)
            self.assertEqual(fused[0].source, FindingSource.fused)
        asyncio.run(run_test())

    def test_case2_same_file_different_rule_ids(self):
        """Case 2: Same file, different rule IDs -> 2 separate findings."""
        async def run_test():
            f1 = Finding(
                source=FindingSource.semgrep,
                severity=Severity.high,
                title="Insecure MD5",
                description="MD5 hash used.",
                file="app.py",
                line_start=10,
                line_end=10,
                confidence=0.8,
                rule_id="rule-md5"
            )
            f2 = Finding(
                source=FindingSource.semgrep,
                severity=Severity.critical,
                title="SQL Injection",
                description="Raw query formatting.",
                file="app.py",
                line_start=50,
                line_end=52,
                confidence=0.9,
                rule_id="rule-sqli"
            )
            fused = await fusion.fuse_findings([f1, f2])
            self.assertEqual(len(fused), 2)
        asyncio.run(run_test())

    def test_case3_same_line_different_rule_ids(self):
        """Case 3: Same line, different rule IDs -> 2 separate findings."""
        async def run_test():
            f1 = Finding(
                source=FindingSource.semgrep,
                severity=Severity.medium,
                title="Hardcoded Password",
                description="Hardcoded password string.",
                file="app.py",
                line_start=15,
                line_end=15,
                confidence=0.85,
                rule_id="rule-password"
            )
            f2 = Finding(
                source=FindingSource.semgrep,
                severity=Severity.high,
                title="Hardcoded API Token",
                description="Hardcoded secret token.",
                file="app.py",
                line_start=15,
                line_end=15,
                confidence=0.9,
                rule_id="rule-api-token"
            )
            fused = await fusion.fuse_findings([f1, f2])
            self.assertEqual(len(fused), 2)
        asyncio.run(run_test())

    def test_case4_same_vulnerability_two_sources(self):
        """Case 4: Same vulnerability from 2 sources (Semgrep + LLM mock) -> 1 fused finding."""
        async def run_test():
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
        asyncio.run(run_test())

    def test_case5_osv_package_vulnerabilities_distinct_ghsa(self):
        """Case 5: OSV package vulnerabilities (different GHSA IDs on same package) -> 26 separate findings (not merged into 3)."""
        async def run_test():
            findings = []
            for i in range(1, 27):
                findings.append(Finding(
                    source=FindingSource.osv,
                    severity=Severity.high,
                    title=f"Vulnerability in jinja2 (GHSA-xxxx-{i:04d})",
                    description=f"Vulnerability details {i}",
                    file="requirements.txt",
                    line_start=1,
                    line_end=1,
                    confidence=1.0,
                    cve_id=f"GHSA-xxxx-{i:04d}",
                    package_name="jinja2" if i <= 10 else ("requests" if i <= 21 else "flask"),
                    package_version="2.10.1"
                ))
            fused = await fusion.fuse_findings(findings)
            self.assertEqual(len(fused), 26)
        asyncio.run(run_test())

    def test_case6_metadata_preservation(self):
        """Case 6: Metadata preservation (file, line_start, line_end, severity, confidence, rule_id, cve_id, package_name, package_version, merged description)."""
        async def run_test():
            f1 = Finding(
                source=FindingSource.osv,
                severity=Severity.medium,
                title="Vulnerability in requests (GHSA-req-01)",
                description="OSV finding report",
                file="requirements.txt",
                line_start=1,
                line_end=1,
                confidence=0.8,
                cve_id="GHSA-req-01",
                package_name="requests",
                package_version="2.20.0"
            )
            f2 = Finding(
                source=FindingSource.llm,
                severity=Severity.critical,
                title="Vulnerability in requests (GHSA-req-01)",
                description="LLM verification of vulnerability",
                file="requirements.txt",
                line_start=1,
                line_end=1,
                confidence=0.95,
                cve_id="GHSA-req-01",
                package_name="requests",
                package_version="2.20.0"
            )
            fused = await fusion.fuse_findings([f1, f2])
            self.assertEqual(len(fused), 1)
            mf = fused[0]
            self.assertEqual(mf.file, "requirements.txt")
            self.assertEqual(mf.line_start, 1)
            self.assertEqual(mf.line_end, 1)
            self.assertEqual(mf.severity, Severity.critical)
            self.assertAlmostEqual(mf.confidence, 0.99, places=2)
            self.assertEqual(mf.cve_id, "GHSA-req-01")
            self.assertEqual(mf.package_name, "requests")
            self.assertEqual(mf.package_version, "2.20.0")
            self.assertIn("OSV finding report", mf.description)
            self.assertIn("LLM verification of vulnerability", mf.description)
        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
