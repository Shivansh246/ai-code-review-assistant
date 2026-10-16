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
    if not isinstance(vuln, dict):
        return Severity.info

    # 1. Try database_specific tags
    database_specific = vuln.get("database_specific")
    if isinstance(database_specific, dict):
        sev_str = database_specific.get("severity") or database_specific.get("github_reviewed_severity")
        if isinstance(sev_str, str) and sev_str.strip():
            return _map_severity(sev_str.strip())

    # 2. Try ecosystem_specific tags
    ecosystem_specific = vuln.get("ecosystem_specific")
    if isinstance(ecosystem_specific, dict):
        sev_str = ecosystem_specific.get("severity")
        if isinstance(sev_str, str) and sev_str.strip():
            return _map_severity(sev_str.strip())

    # 3. Try severity array (CVSS score check)
    severities = vuln.get("severity")
    if isinstance(severities, list):
        for sev in severities:
            if isinstance(sev, dict):
                score_val = sev.get("score")
                if isinstance(score_val, (int, float)):
                    return _parse_cvss_to_severity(float(score_val))

    return Severity.high

def _parse_package_json(content: str) -> List[Dict[str, str]]:
    deps = []
    if not content or not content.strip():
        return deps
    try:
        data = json.loads(content)
        if not isinstance(data, dict):
            return deps
        for section in ["dependencies", "devDependencies", "peerDependencies"]:
            section_data = data.get(section)
            if isinstance(section_data, dict):
                for name, version in section_data.items():
                    if isinstance(name, str) and isinstance(version, str):
                        clean_version = re.sub(r'^[~^><=]+', '', version).strip()
                        if clean_version:
                            deps.append({
                                "package": {"name": name, "ecosystem": "npm"},
                                "version": clean_version
                            })
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse package.json: {e}")
    except Exception as e:
        logger.error(f"Unexpected error parsing package.json: {e}")
    return deps

def _parse_requirements_txt(content: str) -> List[Dict[str, str]]:
    deps = []
    pattern = re.compile(r'^\s*([a-zA-Z0-9_\-\.]+)\s*(?:\[.*\])?\s*==\s*([a-zA-Z0-9_\.\-]+)')
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
        
        if in_require_block or line.startswith('require '):
            parts = line.split()
            if line.startswith('require '):
                parts = parts[1:]
            if len(parts) >= 2:
                name = parts[0]
                version = parts[1]
                deps.append({
                    "package": {"name": name, "ecosystem": "Go"},
                    "version": version
                })
    return deps

def _parse_pom_xml(content: str) -> List[Dict[str, str]]:
    deps = []
    try:
        root = ET.fromstring(content)
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
                g = group_id.text.strip() if group_id.text else ""
                a = artifact_id.text.strip() if artifact_id.text else ""
                v = version.text.strip() if version.text else ""
                if g and a and v and not v.startswith('${'):
                    deps.append({
                        "package": {"name": f"{g}:{a}", "ecosystem": "Maven"},
                        "version": v
                    })
    except ET.ParseError as e:
        logger.error(f"Failed to parse pom.xml: {e}")
    except Exception as e:
        logger.error(f"Unexpected error parsing pom.xml: {e}")
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
        return []

    if not queries:
        return []

    findings = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            payload = {"queries": queries}
            response = await client.post(OSV_BATCH_QUERY_URL, json=payload)
            response.raise_for_status()
            res_data = response.json()
            if not isinstance(res_data, dict):
                logger.error("OSV API returned non-object JSON response.")
                return []
            results = res_data.get("results", [])
            if not isinstance(results, list):
                logger.error("OSV API returned non-list results.")
                return []
            
            for i, result in enumerate(results):
                if i >= len(queries):
                    break
                if not isinstance(result, dict):
                    continue
                vulns = result.get("vulns", [])
                if not isinstance(vulns, list):
                    continue
                query = queries[i]
                pkg_name = query.get("package", {}).get("name", "Unknown")
                pkg_version = query.get("version", "Unknown")
                
                for vuln in vulns:
                    if not isinstance(vuln, dict):
                        continue
                    vuln_id = vuln.get("id", "UNKNOWN")
                    db_spec = vuln.get("database_specific") if isinstance(vuln.get("database_specific"), dict) else {}
                    summary = (
                        vuln.get("summary")
                        or vuln.get("details")
                        or db_spec.get("display_name")
                        or f"Known vulnerability {vuln_id} affecting {pkg_name} {pkg_version}."
                    )
                    severity = _extract_severity(vuln)
                    
                    finding = Finding(
                        file=file_path,
                        line_start=1,
                        line_end=1,
                        title=f"Vulnerability in {pkg_name} ({vuln_id})",
                        description=str(summary),
                        severity=severity,
                        source=FindingSource.osv,
                        confidence=1.0,
                        cve_id=vuln_id,
                        package_name=pkg_name,
                        package_version=pkg_version
                    )
                    findings.append(finding)
    except httpx.TimeoutException as e:
        logger.warning(f"Timeout querying OSV API for {file_path}: {e}")
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
