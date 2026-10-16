# Multimodal Experiment: Audio-Code Context Fusion

## Overview
This research experiment evaluates whether combining code context with voice command transcripts improves intent classification for ambiguous developer utterances over voice transcript alone.

### Important Note on Modality
This is a **transcript-only versus transcript-plus-code-context textual feature fusion** benchmark. It extracts token n-gram features from text transcripts (via `HashingVectorizer`) rather than processing raw acoustic audio waveforms or mel-spectrogram embeddings.

## Problem Statement
Developer voice commands are frequently ambiguous (e.g. "check this", "what is wrong here", "fix this"). Without code context, an intent classifier cannot reliably tell whether the developer wants to `review` clean code, `explain` an active vulnerability finding, or `generate_fix` for a flagged bug. By fusing code context with the utterance transcript, the multimodal model disambiguates developer intent.

## How to Run
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Generate the dataset (optional, will be auto-generated during evaluation):
   ```bash
   python generate_dataset.py
   ```
3. Run the experiment:
   ```bash
   python run_experiment.py
   ```

## Empirical Results
On the 60-sample test set (20 samples per class):

| Metric | Baseline A (Transcript-only) | Approach B (Transcript + Code Fusion) |
| :--- | :---: | :---: |
| **Accuracy** | 51.67% | **100.00%** |
| **Macro F1** | 0.5167 | **1.0000** |
| **Macro Precision** | 0.5167 | **1.0000** |
| **Macro Recall** | 0.5167 | **1.0000** |
| **Weighted F1** | 0.5167 | **1.0000** |

Per-Class F1 Breakdown:
- `explain`: Baseline = 0.5000 vs. Fusion = **1.0000**
- `generate_fix`: Baseline = 0.5500 vs. Fusion = **1.0000**
- `review`: Baseline = 0.5000 vs. Fusion = **1.0000**

Baseline A struggles (~51.7% accuracy) because identical utterances appear across multiple intent classes. Approach B achieves 100% on the synthetic benchmark because code context provides distinct discriminative signals. Detailed outputs are saved in `results_comparison.json`.

## Limitations & Future Work
- **Feature Extraction**: Currently relies on token n-gram HashingVectorizer as a text proxy. Future work should evaluate actual acoustic audio embeddings (e.g. Whisper encoder representations) paired with CodeBERT/AST embeddings.
- **Dataset**: Built on a synthetic dataset of ambiguous developer utterances. Needs validation on real-world voice+code interaction logs from active IDE telemetry.
- **Model Architecture**: Uses a 2-layer MLP classifier. Could be upgraded to cross-attention mechanisms for deeper token-level fusion.
