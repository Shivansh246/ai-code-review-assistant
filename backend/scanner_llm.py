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
    prompt = f"""
Perform a semantic security code analysis on the following code snippet from the file `{file_path}`.
Identify any security vulnerabilities, bugs, or bad practices.

Return the results ONLY as a valid JSON array of objects. Do not include any other text.
Each object in the array must have the following fields:
- "title": a short title for the finding
- "description": a detailed description of the issue
- "severity": one of "critical", "high", "medium", "low", "info"
- "line_start": the line number where the issue starts
- "line_end": the line number where the issue ends
- "confidence": a float between 0.0 and 1.0 representing your confidence in the finding
- "rule_id": (optional) an identifier for the rule or type of issue

Code:
```
{content}
```
"""
    response_text = await _call_llm(prompt)
    if not response_text:
        return []
        
    cleaned_json = _clean_json_response(response_text)
    
    try:
        data = json.loads(cleaned_json)
        if not isinstance(data, list):
            # Sometimes LLM returns a single object instead of array
            if isinstance(data, dict):
                data = [data]
            else:
                logger.error("LLM returned JSON that is not an array or object.")
                return []
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM JSON response: {e}. Raw response: {response_text}")
        return []

    findings = []
    for item in data:
        try:
            severity_str = item.get("severity", "info").lower()
            try:
                severity = Severity(severity_str)
            except ValueError:
                severity = Severity.info

            finding = Finding(
                title=item.get("title", "Unknown finding"),
                description=item.get("description", ""),
                severity=severity,
                line_start=item.get("line_start", 1),
                line_end=item.get("line_end", 1),
                confidence=float(item.get("confidence", 0.8)),
                rule_id=item.get("rule_id", "llm-semantic-issue"),
                source=FindingSource.llm,
                file=file_path
            )
            findings.append(finding)
        except Exception as e:
            logger.warning(f"Failed to parse a finding item: {e}. Item: {item}")
            
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
