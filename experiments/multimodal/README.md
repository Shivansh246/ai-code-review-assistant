# Multimodal Experiment: Audio-Code Fusion

## Overview
This is a research experiment for the AI Code Review Assistant that fuses audio (voice command) embeddings with code embeddings to classify security-relevant voice+code interactions. It tests whether combining a voice command and a code context can successfully predict if the interaction is relevant.

## Why
Multimodal AI integration for code review provides a more fluid developer experience. By combining voice context with the code context, we can intuitively filter out irrelevant commands and focus the AI on relevant interactions.

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

## Expected Results
You will see accuracy, precision, recall, and F1 scores printed to the console, along with a confusion matrix. The results are also saved in `results.json`. Given the simple synthetic dataset, the model should achieve very high accuracy easily.

## Limitations & Future Work
- **Feature Extraction**: Currently relies on simple text-based HashingVectorizer as a proxy. Future work should use actual audio embeddings (e.g., Whisper) and robust code embeddings (e.g., CodeBERT).
- **Dataset**: Built on a small, synthetic dataset. Needs real-world voice+code interaction logs.
- **Model Architecture**: Uses a simple MLP. Could be upgraded to cross-attention mechanisms for deeper fusion.
