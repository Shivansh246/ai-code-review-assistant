# Directives

This folder contains **Layer 1** of the 3-layer architecture: SOPs written in Markdown.

## Purpose

Each directive file describes *what to do* for a specific workflow:
- The goal and context
- Required inputs
- Which execution script(s) to call and with what arguments
- Expected outputs
- Edge cases and known gotchas

## How to write a directive

```markdown
# [Workflow Name]

## Goal
One-sentence description of what this workflow accomplishes.

## Inputs
- `input_a` – description and format
- `input_b` – description and format

## Execution Script
`execution/script_name.py`

## Steps
1. Step one — what to pass in, what to expect back
2. Step two — ...

## Output
What gets produced, where it ends up (e.g. Google Sheet URL).

## Edge Cases
- Known API limits, timing issues, failure modes
- What to do when X goes wrong
```

## Naming convention

Use lowercase with underscores: `scrape_website.md`, `generate_report.md`, etc.

## Living documents

Directives are updated as you learn. When a script changes behavior or a new edge case is discovered, update the relevant directive so the system stays accurate.
