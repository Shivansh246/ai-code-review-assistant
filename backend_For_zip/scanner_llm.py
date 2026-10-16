import asyncio
import os
import json
import logging
import re
from typing import List, Tuple, Any

try:
    from google import genai
except ImportError:
    genai = None

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None

from models import Finding, FindingSource, Severity, TokenAttribution

logger = logging.getLogger(__name__)

def _get_provider_and_model():
    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    model = os.getenv("LLM_MODEL")
    if not model:
        if provider == "openai":
            model = "gpt-4o"
        else:
            model = "gemini-2.0-flash"
    return provider, model

def _clean_json_response(text: str) -> str:
    """Removes markdown formatting and other noise from LLM JSON responses."""
    text = text.strip()
    # Remove markdown code blocks
    text = re.sub(r"^```[a-zA-Z]*\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"```$", "", text, flags=re.MULTILINE)
    return text.strip()

async def _call_llm(prompt: str) -> str:
    provider, model = _get_provider_and_model()
    
    if provider == "openai":
        if AsyncOpenAI is None:
            logger.error("openai package is not installed.")
            return ""
        
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set.")
            return ""
            
        try:
            client = AsyncOpenAI(api_key=api_key)
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            return ""
            
    else:  # default to gemini
        if genai is None:
            logger.error("google-genai package is not installed.")
            return ""
            
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.warning("GEMINI_API_KEY not set.")
            return ""
            
        try:
            client = genai.Client(api_key=api_key)
            if hasattr(client, "aio") and hasattr(client.aio, "models"):
                response = await client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                )
            else:
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=model,
                    contents=prompt,
                )
            return response.text or ""
        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            return ""

async def scan_file(file_path: str, content: str) -> List[Finding]:
    if not content or not content.strip():
        return []

    lines = content.splitlines()
    total_lines = max(1, len(lines))

    prompt = f"""You are an expert static application security testing (SAST) auditor.
Perform a rigorous semantic security code analysis on the code snippet from `{file_path}`.

Analyze for:
- Data flow & user-controlled input reaching dangerous sinks
- Authentication & authorization flaws
- Injection vulnerabilities (SQLi, Command Injection, Code Injection)
- Insecure deserialization
- Sensitive data exposure & hardcoded credentials/secrets
- Cryptographic misuse
- Server-Side Request Forgery (SSRF)
- Path traversal & insecure file operations
- Unsafe subprocess / command execution
- Logic or access control flaws

Requirements:
- Only report genuine security issues backed by clear code evidence.
- Do NOT report stylistic or performance issues unless they have direct security implications.
- Distinguish actual vulnerabilities from suspicious but safe code.
- Return ONLY a valid JSON array of objects without markdown formatting or preamble/postscript.

Each object MUST contain:
- "title": concise, descriptive security title (string)
- "description": clear explanation of why it is vulnerable and how data flows (string)
- "severity": one of "critical", "high", "medium", "low", "info"
- "line_start": 1-indexed starting line number of the vulnerability (integer between 1 and {total_lines})
- "line_end": 1-indexed ending line number of the vulnerability (integer between 1 and {total_lines})
- "confidence": float between 0.0 and 1.0 (e.g. 0.85)
- "rule_id": string identifier for the vulnerability class (e.g. "llm-sqli", "llm-hardcoded-secret")

Code to analyze:
```
{content}
```
"""
    try:
        response_text = await _call_llm(prompt)
    except Exception as e:
        logger.error(f"Error calling LLM provider: {e}")
        return []

    if not response_text:
        return []

    cleaned_json = _clean_json_response(response_text)

    try:
        data = json.loads(cleaned_json)
        if not isinstance(data, list):
            if isinstance(data, dict):
                data = [data]
            else:
                logger.error("LLM returned JSON that is not an array or object.")
                return []
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM JSON response: {e}. Raw response snippet: {cleaned_json[:200]}")
        return []

    findings = []
    for item in data:
        if not isinstance(item, dict):
            continue

        try:
            # 1. Normalize severity
            sev_raw = str(item.get("severity", "info")).strip().lower()
            try:
                severity = Severity(sev_raw)
            except ValueError:
                severity = Severity.info

            # 2. Validate and clamp line numbers
            try:
                l_start = int(item.get("line_start", 1))
            except (ValueError, TypeError):
                l_start = 1

            try:
                l_end = int(item.get("line_end", l_start))
            except (ValueError, TypeError):
                l_end = l_start

            l_start = max(1, min(total_lines, l_start))
            l_end = max(l_start, min(total_lines, l_end))

            # 3. Validate and clamp confidence
            try:
                conf = float(item.get("confidence", 0.8))
            except (ValueError, TypeError):
                conf = 0.8
            conf = max(0.0, min(1.0, conf))

            # 4. Mandatory title and description
            title = str(item.get("title", "")).strip() or "LLM Semantic Security Finding"
            description = str(item.get("description", "")).strip() or "Potential security issue detected by LLM semantic scanner."
            rule_id = str(item.get("rule_id", "")).strip() or "llm-semantic-issue"

            finding = Finding(
                title=title,
                description=description,
                severity=severity,
                line_start=l_start,
                line_end=l_end,
                confidence=conf,
                rule_id=rule_id,
                source=FindingSource.llm,
                file=file_path
            )
            findings.append(finding)
        except Exception as e:
            logger.warning(f"Failed to normalize LLM finding item: {e}. Item: {item}")

    return findings

async def explain_finding(finding: Finding, code_context: str) -> Tuple[str, List[TokenAttribution], List[int]]:
    prompt = f"""
Explain the following security finding in the provided code context.

Finding Title: {finding.title}
Finding Description: {finding.description}
File: {finding.file}
Lines: {finding.line_start} to {finding.line_end}

Code Context:
```
{code_context}
```

Return the response ONLY as a valid JSON object. Do not include any other text.
The JSON object must have these fields:
- "explanation": A natural language explanation of WHY the finding is a security issue
- "token_attributions": A list of objects with fields "token" (the important code snippet) and "importance" (a float 0-1)
- "highlighted_lines": A list of integers representing the line numbers that are most relevant

JSON:
"""
    response_text = await _call_llm(prompt)
    if not response_text:
        return "Could not generate explanation due to API error.", [], []

    cleaned_json = _clean_json_response(response_text)
    
    try:
        data = json.loads(cleaned_json)
        explanation = data.get("explanation", "No explanation provided.")
        attributions_data = data.get("token_attributions", [])
        highlighted_lines = data.get("highlighted_lines", [])
        
        token_attributions = []
        for attr in attributions_data:
            try:
                token_attributions.append(TokenAttribution(
                    token=str(attr.get("token", "")),
                    importance=float(attr.get("importance", 0.5))
                ))
            except Exception:
                pass
                
        return explanation, token_attributions, highlighted_lines
    except Exception as e:
        logger.error(f"Failed to parse LLM explanation JSON: {e}")
        return "Failed to parse explanation from LLM.", [], []

async def suggest_fix(finding: Finding, code_context: str) -> Tuple[str, float]:
    prompt = f"""
Suggest a fix for the following security finding in the provided code context.

Finding Title: {finding.title}
Finding Description: {finding.description}
File: {finding.file}
Lines: {finding.line_start} to {finding.line_end}

Code Context:
```
{code_context}
```

Return the response ONLY as a valid JSON object. Do not include any other text.
The JSON object must have these fields:
- "fix_code": The entire updated code block replacing the vulnerable part or a complete file if small enough.
- "confidence": A float between 0.0 and 1.0 representing your confidence in this fix.

JSON:
"""
    response_text = await _call_llm(prompt)
    if not response_text:
        return "", 0.0

    cleaned_json = _clean_json_response(response_text)
    
    try:
        data = json.loads(cleaned_json)
        fix_code = data.get("fix_code", "")
        confidence = float(data.get("confidence", 0.0))
        return fix_code, confidence
    except Exception as e:
        logger.error(f"Failed to parse LLM fix JSON: {e}")
        return "", 0.0
