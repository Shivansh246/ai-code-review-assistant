# Backend API — AI Code Review Assistant

## Goal
Serve the FastAPI backend that the VS Code extension connects to. Provides security analysis via Semgrep, LLM, and OSV scanners, with finding fusion, risk scoring, XAI explanations, and adaptive feedback.

## Owner
Gulshan (backend engineer)

## Repository path
`backend/` — all Python source lives here.

## Architecture
```
POST /review → [Semgrep | LLM | OSV] → Fusion → Scoring → ReviewResponse
POST /explain → XAI engine → ExplainResponse
POST /fix → LLM fix generator → FixResponse
POST /feedback → SQLite persistence + RL update
POST /voice → Logging for metrics
```

## Endpoints

| Method | Path | Request | Response | Purpose |
|---|---|---|---|---|
| GET | /health | — | `{status, service}` | Extension health check |
| POST | /review | `{file_path, content}` | `ReviewResponse` | Single-file review |
| POST | /review/repo | `{repo_path}` | `ReviewResponse` | Full repo review |
| POST | /explain | `{finding_id}` | `ExplainResponse` | XAI explanation |
| POST | /fix | `{finding_id, content}` | `FixResponse` | AI fix generation |
| POST | /feedback | `FeedbackEvent` | `{status}` | Accept/reject |
| POST | /voice | `VoiceCommand` | `{status}` | Voice command log |

## Key files

| File | Purpose |
|---|---|
| `main.py` | FastAPI app, router, lifespan management |
| `models.py` | Pydantic schemas (sync with `types.ts`) |
| `scanner_semgrep.py` | Semgrep CLI integration |
| `scanner_llm.py` | LLM security analysis (Gemini/OpenAI) |
| `scanner_osv.py` | OSV dependency CVE scanner |
| `fusion.py` | Finding deduplication and merging |
| `scoring.py` | Risk scoring and ranking |
| `xai.py` | XAI explanation and attribution generation |
| `feedback.py` | SQLite feedback persistence |
| `rl_experiment.py` | Contextual bandit RL experiment |

## Setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Copy and fill in API keys
cp ../.env.example ../.env

# Run the server
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## Schema contract
`models.py` mirrors `vscode-extension/src/types.ts` field-for-field.
When updating either side, update the other.

## Edge cases
- Semgrep not installed → scanner returns empty list, logs warning
- LLM API key not set → scanner returns empty list, logs warning
- OSV API unreachable → scanner returns empty list, logs warning
- All scanners fail → returns empty findings list (never errors)
- Finding not in cache for /explain → returns 404
- Feedback on unknown finding → still persisted (finding_id recorded)
