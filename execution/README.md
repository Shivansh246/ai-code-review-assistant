# Execution Scripts

This folder contains **Layer 3** of the 3-layer architecture: deterministic Python scripts.

## Purpose

Each script does exactly one thing reliably:
- API calls, data processing, file operations, database interactions
- Reads config from `.env` via `python-dotenv`
- Accepts arguments via CLI (`argparse`) or stdin/config dict
- Prints structured output (JSON or plain text) to stdout
- Exits with code `0` on success, non-zero on failure

## Conventions

| Convention | Detail |
|---|---|
| **One responsibility** | Each script does one job |
| **CLI args** | Use `argparse` for inputs; avoid hardcoded paths |
| **Env vars** | Load secrets from `.env` with `python-dotenv` |
| **Logging** | Print progress to stderr, results to stdout |
| **Error handling** | Catch exceptions, print stack trace, exit non-zero |
| **Comments** | Every function/section commented; new devs can follow |

## Template

```python
#!/usr/bin/env python3
"""
script_name.py – One-line description of what this script does.

Usage:
    python execution/script_name.py --arg1 value --arg2 value
"""

import argparse
import sys
from dotenv import load_dotenv
import os

load_dotenv()

def main(arg1: str, arg2: str) -> None:
    """Core logic here."""
    # ... implementation ...
    print("result")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arg1", required=True, help="Description of arg1")
    parser.add_argument("--arg2", required=True, help="Description of arg2")
    args = parser.parse_args()

    try:
        main(args.arg1, args.arg2)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
```

## Naming convention

Use lowercase with underscores matching the directive: `scrape_single_site.py`, `generate_report.py`, etc.
