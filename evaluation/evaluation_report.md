# AI Code Review Assistant — Evaluation Report

**Date:** 2026-10-16  
**Author:** Automated evaluation run  
**Hardware:** Intel Core i5-1135G7, 8 GB RAM, Arch Linux (kernel 6.18.48-1-lts)  
**Software:** Python 3.14.7, Node 26.8.1, npm 12.0.2  
**Repository state:** All changes uncommitted; `git diff --check` — CLEAN

---

## 1. Experimental Setup

The AI Code Review Assistant combines three analysis layers:
1. **Semgrep** — static rule-based vulnerability detection  
2. **OSV API** — open-source vulnerability database for dependency scanning  
3. **LLM scanner** — semantic analysis (disabled in this run; no API key present)

All experiments were executed locally on the development machine. No paid external services were invoked. The FastAPI backend (`http://localhost:8000`) was started before evaluations that required live HTTP calls.

### 1.1 Limitations acknowledged upfront
| Limitation | Impact |
|-----------|--------|
| LLM scanner disabled (no API key) | Security detection is Semgrep + OSV only; LLM findings not counted |
| No `/deps/scan` HTTP endpoint | OSV dependency scan invoked directly via `scanner_osv.scan_file()` |
| Multimodal dataset is synthetic | Results cannot be generalised to real-world acoustic voice data |
| Physical microphone not tested | Voice accuracy is mocked-test coverage only |
| Ground-truth matching: line-overlap | XSS, hardcoded credentials may be missed if Semgrep fires on different lines |

---

## 2. Phase 2 — Test Suite Results

All three test suites passed with zero failures.

| Suite | Command | Total | Passed | Failed | Skipped | Duration |
|-------|---------|-------|--------|--------|---------|----------|
| Backend (Python unittest) | `PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend -v` | 102 | **102** | 0 | 0 | 0.451 s |
| Extension compilation (TypeScript) | `cd vscode-extension && npm run compile` | — | **0 TS errors** | — | — | — |
| Voice tests (Node.js) | `node ./out/test/runVoiceTests.js` | 107 | **107** | 0 | — | < 1 s |

> **Note on voice tests:** All 107 voice tests are mocked. They exercise transcript parsing, command dispatch, status-bar lifecycle, VAD reject, wake-word bypass, recorder fallback (SIGINT, ENOENT), HTTP error handling (503, connection-refused), and empty-transcription paths. No physical microphone is used.

---

## 3. Phase 3 — Vulnerability Detection Evaluation

### 3.1 Dataset
- **Samples:** 2 files (`vulnerable_app.py`, `vulnerable_app.js`)
- **Ground truth:** 14 labeled findings (8 Python, 6 JavaScript)
- **Categories:** hardcoded_credentials, sql_injection, command_injection, insecure_deserialization, path_traversal, weak_cryptography, ssrf, xss, prototype_pollution, insecure_eval, missing_auth
- **Backend request:** `POST /review` with full file code
- **Matching procedure:** Line-overlap — a finding is a TP if its (file, line_start, line_end) overlaps any unmatched ground-truth annotation

### 3.2 Results (Semgrep + OSV, LLM disabled)

| Metric | Value |
|--------|-------|
| True Positives (TP) | **8** |
| False Positives (FP) | **16** |
| False Negatives (FN) | **6** |
| Precision | **0.333** |
| Recall | **0.571** |
| F1 Score | **0.421** |

**Figure:** `evaluation/results/charts/security_detection_metrics.png`  
**Figure:** `evaluation/results/charts/security_by_severity.png`

### 3.3 Discussion
- Recall (0.571) exceeds precision (0.333) because Semgrep fires many rules not in the 14-item ground truth, generating FPs.
- 6 FNs include categories that Semgrep's bundled rules do not cover reliably in these specific code patterns (e.g., XSS in Python f-strings, SSRF detection).
- LLM scanner would be expected to recover additional semantic FNs (particularly XSS and SSRF) but was unavailable.
- **Data file:** `evaluation/results/security_metrics.json`

---

## 4. Phase 4 — Dependency Scanning Evaluation

### 4.1 Dataset
- **Manifest:** `evaluation/test_samples/vulnerable_requirements.txt`
- **Packages:** `jinja2==2.10.1`, `requests==2.20.0`, `flask==0.12`
- **API:** OSV Batch Query endpoint (`https://api.osv.dev/v1/querybatch`)

### 4.2 Results
 
| Metric | Value |
|--------|-------|
| Packages checked | 3 (`jinja2==2.10.1`, `requests==2.20.0`, `flask==0.12`) |
| Confirmed affected packages | 3 / 3 (100%) |
| Total OSV query records returned | **26** |
| Unique vulnerability clusters (deduplicated by CVE/GHSA alias) | **13** |
| Execution time | **1.073 s** |
| API errors | 0 |

#### Breakdown by Package & Deduplication:
- **`jinja2==2.10.1`**: 10 advisory records (5 GHSA, 5 PYSEC) mapping to **5 distinct CVEs** (`CVE-2025-27516`, `CVE-2020-28493`, `CVE-2024-22195`, `CVE-2024-34064`, `CVE-2024-56326`).
- **`requests==2.20.0`**: 8 advisory records (4 GHSA, 4 PYSEC) mapping to **4 distinct CVEs** (`CVE-2024-47081`, `CVE-2024-35195`, `CVE-2026-25645`, `CVE-2023-32681`).
- **`flask==0.12`**: 8 advisory records (4 GHSA, 4 PYSEC) mapping to **4 distinct CVEs** (`CVE-2018-1000656`, `CVE-2019-1010083`, `CVE-2026-27205`, `CVE-2023-30861`).

All 3 package versions tested are confirmed vulnerable. OSV returns dual records per flaw (one from GitHub Advisory Database `GHSA-*` and one from the Python Packaging Advisory Database `PYSEC-*`). In total, exactly **13 unique vulnerability clusters** affect the 3 pinned packages.

> **Note:** No `/deps/scan` HTTP route exists in the backend. The OSV scan was run by calling `scanner_osv.scan_file()` directly, which is the same code path used by `/review/repo`.

**Data file:** `evaluation/results/dependency_metrics.json`

---

## 5. Phase 5 — Finding Prioritisation & Feedback Evaluation

### 5.1 Setup
- **Fixture:** 5 synthetic findings (3 TPs, 2 FPs) from `eval_comparison.py`
- **Ground truth:** 3 TPs (f1-cmd-tp, f3-creds-tp, f4-path-tp)
- **Feedback sequence:** 3 rejections on `sqli-fp`, 2 acceptances on `path-traversal` (deterministic, seed=42)
- **Methods compared:** Static Baseline, Phase 6A Adaptive, RL Cold-Start (0 events), RL Trained (5 events)
- **Metric:** Top-3 Precision, Recall, F1; Top-3 FP count; FP score/rank demotion rates

### 5.2 Results

| Metric | Static | Adaptive (6A) | RL Cold-Start | RL Trained |
|--------|--------|---------------|---------------|------------|
| Top-3 Precision | 0.667 | **1.000** | 0.667 | 0.667 |
| Top-3 Recall | 0.667 | **1.000** | 0.667 | 0.667 |
| Top-3 F1 | 0.667 | **1.000** | 0.667 | 0.667 |
| Top-3 FP count | 1 | **0** | 1 | 1 |
| FP Share w/ Score Reduced (%) | N/A | **100.0%** | 0.0% | **100.0%** |
| FP Mean Numerical Score Reduction (%) | N/A | **60.0%** (81.0 → 32.4) | 0.0% | **40.0%** (81.0 → 48.6) |
| FP Rank Demotion Rate (%) | N/A | **100.0%** (Rank 2 → 5) | 0.0% | **0.0%** (Remains Rank 2) |

*Definitions:*
- **FP Share w/ Score Reduced (%):** Fraction of target false-positive findings whose risk score decreased. Both Adaptive (6A) and RL Trained affected 100% (1/1) of target FP candidates.
- **FP Mean Numerical Score Reduction (%):** Magnitude of risk score drop: Adaptive (6A) reduced `f2-sqli-fp` by 60.0% (multiplier 0.40); RL Trained reduced all items uniformly by 40.0% (multiplier 0.60).
- **FP Rank Demotion Rate (%):** Fraction of target FPs moved to a worse (lower) rank: Adaptive (6A) dropped the FP from Rank 2 to Rank 5 (100% demotion rate), successfully removing it from Top-3. RL Trained scaled all items uniformly by 0.60, leaving relative ordering unchanged (0% rank demotion).

**Figure:** `evaluation/results/charts/prioritisation_comparison.png`

### 5.3 Discussion
- Phase 6A (deterministic rule-level adaptive scoring) achieves perfect Top-3 Precision/Recall/F1 with this feedback sequence.
- RL Cold-Start correctly degrades to the static baseline (action multiplier = 1.00 for all findings).
- RL Trained with only 5 feedback events learns to apply a uniform 0.60 multiplier to all findings (due to feature generalisation across identical language/scanner context), which reduces absolute scores without changing ranking — ineffective for FP demotion.
- Phase 6A remains the production default. Phase 6B RL is isolated as an experimental opt-in.

**Data file:** `evaluation/results/prioritisation_metrics.json`

---

## 6. Phase 6 — Multimodal Intent Classification Experiment

### 6.1 Dataset Characteristics

> **⚠️ Important:** The dataset is **synthetic**. The `audio` field contains text transcripts of voice commands (e.g., `"run a check on this"`), not acoustic waveforms. Results measure text-level classification, not speech recognition accuracy.

| Property | Value |
|----------|-------|
| Total samples | 300 |
| Classes | `review`, `explain`, `generate_fix` |
| Class distribution | 100 per class (balanced) |
| Split | 80/20 stratified (seed=42) |
| Train / Test | 240 / 60 |
| Data generation | `generate_dataset.py` with seed=42 |
| Leakage check | Deterministic stratified split; no shared samples |

### 6.2 Results

| Metric | Baseline A (Transcript Only) | Approach B (+ Code Context) |
|--------|------------------------------|------------------------------|
| Accuracy | 0.5167 | **1.0000** |
| F1 (Macro) | 0.5167 | **1.0000** |
| Precision (Macro) | 0.5167 | **1.0000** |
| Recall (Macro) | 0.5167 | **1.0000** |
| F1 — explain | 0.5000 | 1.0000 |
| F1 — generate_fix | 0.5500 | 1.0000 |
| F1 — review | 0.5000 | 1.0000 |

**Figure:** `evaluation/results/charts/intent_classification.png`

### 6.3 Scientific Root-Cause Analysis of 100% Accuracy
- **Baseline A (TF-IDF on transcript text alone)** achieves **51.67% accuracy**. Developer command utterances in this dataset were purposely chosen to be highly ambiguous (`"check this"`, `"look at this"`, `"process this"` occur across all three classes), demonstrating that voice transcript alone is fundamentally insufficient to resolve user intent.
- **Approach B (TF-IDF on transcript + code context)** achieves **100.00% accuracy (60/60 test samples)**. While statistically genuine on this test set, the 100% score is a product of dataset construction rather than genuine acoustic-multimodal generalization:
  1. **Template Determinism & High Repetition:** The 300 dataset samples were generated by randomly pairing 14 command templates with only **15 total code context templates** (5 per class). Each class's code context pool contains completely disjoint keyword sets:
     - `generate_fix`: Every single code snippet contains the explicit comments `# Target: Remediation needed`, `# Issue:`, and `# Fix plan:`.
     - `explain`: Every code snippet begins with `# Finding ID:`, `# Rule:`, and `# Description:`.
     - `review`: Code snippets are pure unannotated functions (`def fetch_user_data`, `calculate_hypotenuse`, etc.) with zero vulnerability metadata headers.
  2. **Direct Label Leakage via Annotation Tokens:** The TF-IDF vectorizer extracts n-grams from these header comments (`"target"`, `"remediation"`, `"finding"`, `"rule"`), which are linearly separable with 100% margin.
  3. **No Acoustic Modeling:** The input feature `audio` consists of normalized ASCII transcript strings, bypassing all acoustic noise, speaker variability, and speech-to-text error cascades.
- **Scientific Conclusion:** The experiment validly demonstrates that *code context disambiguates ambiguous voice commands in an IDE*. However, the 100% metric must be qualified in the academic report as an artifact of synthetic template discrimination. Real-world code without synthetic finding headers will present higher entropy.

**Data file:** `evaluation/results/intent_metrics.json`

---

## 7. Phase 7 — Voice Pipeline Verification

### 7.1 Results
- **107 / 107 mocked tests passed** (see Phase 2)
- Tests cover: transcript parsing, command dispatch (review/explain/fix), status-bar lifecycle, VAD energy reject, wake-word gating, duplicate session dedup, simulator bypass, recorder fallback (SIGINT, ENOENT spawn), HTTP 503 (model missing), connection-refused, empty WAV, header-only WAV, empty transcript from Vosk.
- Physical microphone tests: **not executed** (not possible in non-interactive environment). Real microphone input was verified manually in a prior session (transcript "hello" and "reveal this" successfully reached `/voice/transcribe` and returned transcripts; command dispatch confirmed for `review` intent).

---

## 8. Phase 8 — Performance Measurements

| Operation | Runs | Min | Mean | Max | Notes |
|-----------|------|-----|------|-----|-------|
| `/health` ping | 5 | 0.42 ms | 2.24 ms | 9.30 ms | Local loopback |
| `/review` (single file, 69 lines) | 3 | 4.462 s | 4.691 s | 4.944 s | Semgrep + OSV; LLM disabled |
| Prioritisation eval | 3 | 1 ms | 1 ms | 1 ms | Pure Python, no network |
| Multimodal experiment | 3 | 1.038 s | 1.068 s | 1.116 s | sklearn TF-IDF + LogReg, 300 samples |

**Figure:** `evaluation/results/charts/performance_latency.png`

> **Notes:**  
> - `/review` latency of ~4.7 s includes Semgrep subprocess spawn and OSV API network call.  
> - LLM scanner disabled; enabling it would add 3–15 s depending on provider and model.  
> - Vosk transcription latency for a ~30 s audio clip was not measured automatically (requires real audio input device).

**Data file:** `evaluation/results/performance_metrics.json`

---

## 9. Generated Artifacts

| File | Description | Status |
|------|-------------|--------|
| `evaluation/results/test_summary.json` | Test suite results, environment versions | ✅ Generated |
| `evaluation/results/security_metrics.json` | TP/FP/FN, precision, recall, F1, severity breakdown | ✅ Generated |
| `evaluation/results/dependency_metrics.json` | OSV scan of 3 packages, 26 advisories | ✅ Generated |
| `evaluation/results/prioritisation_metrics.json` | 4-method comparison, demotion rates | ✅ Generated |
| `evaluation/results/intent_metrics.json` | Multimodal intent experiment results + dataset info | ✅ Generated |
| `evaluation/results/performance_metrics.json` | Wall-clock timings for 4 operations | ✅ Generated |
| `evaluation/results/charts/security_detection_metrics.png` | TP/FP/FN bar chart + Precision/Recall/F1 | ✅ Generated |
| `evaluation/results/charts/security_by_severity.png` | Detection results grouped by severity | ✅ Generated |
| `evaluation/results/charts/prioritisation_comparison.png` | 4-method Top-3 comparison + FP count | ✅ Generated |
| `evaluation/results/charts/intent_classification.png` | Baseline vs Fusion overall + per-class F1 | ✅ Generated |
| `evaluation/results/charts/performance_latency.png` | /review and /health latency bar charts | ✅ Generated |

---

## 10. Supported and Unsupported Claims

### Supported by measured evidence
- Backend test suite: **102/102 passed** (deterministic, offline)
- Extension voice tests: **107/107 passed** (mocked)
- Adaptive prioritisation (Phase 6A): Top-3 **F1 = 1.00** with 5 synthetic feedback events
- Static baseline prioritisation: Top-3 **F1 = 0.667**
- Multimodal fusion (+code context): **100% accuracy** on synthetic balanced dataset (N=60 test)
- Transcript-only baseline: **52% accuracy** on same dataset
- OSV dependency scan: **26 advisories** for 3 confirmed-vulnerable PyPI packages
- /review latency: **mean 4.69 s** (Semgrep + OSV, LLM disabled)

### Not supported / limitations
- End-to-end detection recall for full repositories (not measured — scanner not run on a labelled repo)
- LLM-scanner contribution to precision/recall (API key unavailable)
- Physical microphone transcription accuracy (cannot be measured without interactive session)
- Multimodal results on real speech data (dataset is synthetic text fixtures)
- RL experiment results at scale (only 5 synthetic feedback events, 5 candidate findings)
- Vosk transcription latency at known audio lengths (not benchmarked automatically)

---

## 11. Threats to Validity

1. **Ground-truth coverage:** 14 findings across 2 small synthetic files is a minimal dataset. Results may not generalise to larger or real codebases.
2. **LLM unavailability:** The LLM scanner is a core component but produced no findings. Precision/recall figures reflect Semgrep + OSV only.
3. **Synthetic intent dataset:** The 300-sample dataset is generated by `generate_dataset.py` with correlated code snippets. The 100% fusion accuracy is likely an artifact of feature correlation by construction.
4. **Single evaluation run:** No confidence intervals or cross-validation were performed on the intent experiment (though it is deterministic with seed=42).
5. **Line-overlap matching:** The matching procedure may count a finding as TP even if Semgrep fires for a different reason on the same lines.

---

*End of report. No data was fabricated. All measurements were produced by running the actual project code on the specified hardware. Generated: 2026-10-16T15:35+05:30*
