import os
import json
import re
import xml.etree.ElementTree as ET
import logging
import httpx
from typing import List, Dict, Any, Tuple, Optional
from pathlib import Path

from models import Finding, FindingSource, Severity

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

OSV_QUERY_URL = "https://api.osv.dev/v1/query"
OSV_BATCH_QUERY_URL = "https://api.osv.dev/v1/querybatch"

def _map_severity(osv_severity: str) -> Severity:
    osv_severity = osv_severity.upper()
    if osv_severity in ["CRITICAL"]:
        return Severity.critical
    elif osv_severity in ["HIGH"]:
        return Severity.high
    elif osv_severity in ["MODERATE", "MEDIUM"]:
        return Severity.medium
    elif osv_severity in ["LOW"]:
        return Severity.low
    return Severity.info

def _parse_cvss_to_severity(cvss_score: float) -> Severity:
    if cvss_score >= 9.0:
        return Severity.critical
    elif cvss_score >= 7.0:
        return Severity.high
    elif cvss_score >= 4.0:
        return Severity.medium
    elif cvss_score > 0:
        return Severity.low
    return Severity.info

def _extract_severity(vuln: dict) -> Severity:
    # Try to find CVSS score first
    severities = vuln.get("severity", [])
    for sev in severities:
        if sev.get("type") in ("CVSS_V3", "CVSS_V4"):
            # A rough heuristic: check if we can parse the score from the vector string or if a score is provided
            score_str = sev.get("score", "")
            # CVSS vectors don't directly give score without calculation, but OSV sometimes provides numeric score
            # If not, let's rely on database specific tags or fallback
            pass

    # Fallback to checking database specific tags
    database_specific = vuln.get("database_specific", {})
    if "severity" in database_specific:
        return _map_severity(database_specific["severity"])
    
    return Severity.high # Default to high if unknown, better safe than sorry for known CVEs

def _parse_package_json(content: str) -> List[Dict[str, str]]:
    deps = []
    try:
        data = json.loads(content)
        for section in ["dependencies", "devDependencies", "peerDependencies"]:
            if section in data:
                for name, version in data[section].items():
                    # Strip common specifiers to get base version, very naive
                    clean_version = re.sub(r'^[~^><=]+', '', version).strip()
                    deps.append({
                        "package": {"name": name, "ecosystem": "npm"},
                        "version": clean_version
                    })
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse package.json: {e}")
    return deps

def _parse_requirements_txt(content: str) -> List[Dict[str, str]]:
    deps = []
    # Match package==version, ignore comments and extras
    pattern = re.compile(r'^([a-zA-Z0-9_\-]+)(?:\[.*\])?==([a-zA-Z0-9_\.\-]+)')
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = pattern.match(line)
        if match:
            deps.append({
                "package": {"name": match.group(1), "ecosystem": "PyPI"},
                "version": match.group(2)
            })
    return deps

def _parse_go_mod(content: str) -> List[Dict[str, str]]:
    deps = []
    # Match basic `require github.com/foo/bar v1.2.3` and `module v1.2.3` within require blocks
    in_require_block = False
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith('//'):
            continue
        if line.startswith('require ('):
            in_require_block = True
            continue
        if in_require_block and line == ')':
            in_require_block = False
            continue
        
        # single line require or block line
        if in_require_block or line.startswith('require '):
            parts = line.split()
            if line.startswith('require '):
                parts = parts[1:] # skip 'require'
            if len(parts) >= 2:
                name = parts[0]
                version = parts[1]
                # versions might be like v1.2.3+incompatible, keep as is
                deps.append({
                    "package": {"name": name, "ecosystem": "Go"},
                    "version": version
                })
    return deps

def _parse_pom_xml(content: str) -> List[Dict[str, str]]:
    deps = []
    try:
        root = ET.fromstring(content)
        # Handle namespaces if any (simplified)
        ns = ""
        m = re.match(r'\{.*\}', root.tag)
        if m:
            ns = m.group(0)
            
        dependencies = root.findall(f".//{ns}dependency")
        for dep in dependencies:
            group_id = dep.find(f"{ns}groupId")
            artifact_id = dep.find(f"{ns}artifactId")
            version = dep.find(f"{ns}version")
            
            if group_id is not None and artifact_id is not None and version is not None:
                g = group_id.text.strip()
                a = artifact_id.text.strip()
                v = version.text.strip()
                # Ignore properties for now (e.g. ${spring.version})
                if not v.startswith('${'):
                    deps.append({
                        "package": {"name": f"{g}:{a}", "ecosystem": "Maven"},
                        "version": v
                    })
    except ET.ParseError as e:
        logger.error(f"Failed to parse pom.xml: {e}")
    return deps

async def scan_file(file_path: str, content: str) -> List[Finding]:
    filename = os.path.basename(file_path).lower()
    queries = []
    
    if filename == "package.json":
        queries = _parse_package_json(content)
    elif filename == "requirements.txt":
        queries = _parse_requirements_txt(content)
    elif filename == "go.mod":
        queries = _parse_go_mod(content)
    elif filename == "pom.xml":
        queries = _parse_pom_xml(content)
    else:
        # Not a supported manifest file
        return []

    if not queries:
        return []

    findings = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # We can use batch API
            payload = {"queries": queries}
            response = await client.post(OSV_BATCH_QUERY_URL, json=payload)
            response.raise_for_status()
            results = response.json().get("results", [])
            
            for i, result in enumerate(results):
                vulns = result.get("vulns", [])
                query = queries[i]
                pkg_name = query["package"]["name"]
                pkg_version = query["version"]
                
                for vuln in vulns:
                    vuln_id = vuln.get("id", "UNKNOWN")
                    summary = vuln.get("summary", vuln.get("details", "No details available."))
                    severity = _extract_severity(vuln)
                    
                    finding = Finding(
                        file=file_path,
                        line_start=1,
                        line_end=1,
                        title=f"Vulnerability in {pkg_name} ({vuln_id})",
                        description=summary,
                        severity=severity,
                        source=FindingSource.osv,
                        confidence=1.0,
                        cve_id=vuln_id,
                        package_name=pkg_name,
                        package_version=pkg_version
                    )
                    findings.append(finding)
        except httpx.RequestError as e:
            logger.error(f"Network error when querying OSV: {e}")
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error from OSV API: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            logger.exception(f"Unexpected error querying OSV: {e}")
            
    return findings

async def scan_repo(repo_path: str) -> List[Finding]:
    supported_files = {"package.json", "requirements.txt", "go.mod", "pom.xml"}
    all_findings = []
    
    for root, _, files in os.walk(repo_path):
        for file in files:
            if file.lower() in supported_files:
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    # Convert to relative path for better reporting if possible
                    # but model might expect absolute or relative. Keep it as absolute for now, or relative to repo.
                    rel_path = os.path.relpath(file_path, repo_path)
                    findings = await scan_file(rel_path, content)
                    all_findings.extend(findings)
                except Exception as e:
                    logger.error(f"Failed to scan {file_path}: {e}")
                    
    return all_findings
