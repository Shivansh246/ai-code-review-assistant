# Evaluation Infrastructure

This directory contains scripts and test samples for evaluating the AI Code Review Assistant.

## Security Evaluation
To run the security evaluation against the known vulnerable applications:
```bash
python eval_security.py --backend http://localhost:8000
```
This script computes Precision, Recall, and F1 scores based on `test_samples/ground_truth.json`.

## Performance Comparison
To compare the performance of Semgrep-only vs Full Pipeline (Semgrep + LLM + OSV):
```bash
python eval_comparison.py --backend http://localhost:8000
```

## Voice Metrics
Voice interaction metrics are logged in `test_samples/voice_test_log.jsonl` for offline evaluation.
