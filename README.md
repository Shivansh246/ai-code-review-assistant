# AI Project

A 3-layer agentic architecture that separates human intent, AI decision-making, and deterministic execution.

```
AI_Project/
├── agents.md          ← Agent instructions (also mirrored as CLAUDE.md, GEMINI.md)
├── CLAUDE.md          ← Mirror of agents.md (loads in Claude environments)
├── GEMINI.md          ← Mirror of agents.md (loads in Gemini environments)
├── .env               ← Secrets & API keys (gitignored, copy from .env.example)
├── .env.example       ← Safe template for .env
├── directives/        ← Layer 1: SOPs / what-to-do instructions
│   └── README.md
├── execution/         ← Layer 3: Deterministic Python scripts
│   └── README.md
└── .tmp/              ← Intermediate files (gitignored, always regenerated)
```

## The 3 Layers

| Layer | Location | Purpose |
|---|---|---|
| **1 – Directive** | `directives/*.md` | SOPs: what to do, inputs, outputs, edge cases |
| **2 – Orchestration** | The AI agent | Reads directives, routes execution, handles errors |
| **3 – Execution** | `execution/*.py` | Deterministic scripts that do the actual work |

## Getting Started

```bash
# 1. Copy the env template and fill in your keys
cp .env.example .env

# 2. Install Python dependencies for any execution script you use
pip install python-dotenv   # baseline for all scripts
```

## Workflow

1. **New task** → check `directives/` for an existing SOP
2. **No directive** → ask the AI to create one, then run it
3. **Script fails** → AI self-anneals: fixes, retests, updates the directive
4. **Deliverables** live in Google Sheets / Slides / cloud — not local files

See [`agents.md`](agents.md) for full operating principles.
