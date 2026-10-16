# Local Offline Voice Recognition (Vosk) Guide

This document describes the setup, architecture, and operation of the free, 100% offline speech recognition system for the AI Code Review Assistant on Arch Linux.

## 1. Overview & Privacy

- **Zero Cloud Costs:** Speech recognition runs locally via Vosk (Kaldi ASR engine).
- **Zero API Keys:** No OpenAI, Google Cloud, or third-party paid transcription services required.
- **Privacy Guaranteed:** Audio captured from the system microphone is never uploaded to any remote server or logged to disk permanently.
- **Arch Linux Architecture:** Audio capture runs on the native host via `parecord` (PipeWire / PulseAudio) with automatic fallback to `arecord` (ALSA), bypassing upstream VS Code webview iframe permissions-policy blocks.

## 2. Prerequisites & Dependencies

### Arch Linux System Packages
Ensure either `pipewire-pulse` (default on modern Arch/KDE) or `alsa-utils` is installed:
```bash
# Recommended on modern Arch Linux (PipeWire / PulseAudio)
sudo pacman -S pipewire-pulse

# Fallback ALSA recording utility
sudo pacman -S alsa-utils

# Audio format conversion utility
sudo pacman -S ffmpeg
```

### Backend Python Packages
In `./backend/.venv`:
```bash
./backend/.venv/bin/pip install -r backend/requirements.txt
```
*(Includes `vosk>=0.3.45` and `python-multipart>=0.0.9`)*

## 3. One-Time Model Download

The system uses the official lightweight English model (`vosk-model-small-en-us-0.15`), optimized for CPU-only laptops (Intel Core i5, 8 GB RAM):

Run the automated download and verification script:
```bash
./backend/.venv/bin/python backend/download_vosk_model.py
```
To check if the model is installed without re-downloading:
```bash
./backend/.venv/bin/python backend/download_vosk_model.py --check-only
```
Default download location: `backend/models/vosk-model-small-en-us-0.15` (gitignored).
To configure a custom model path, set the `VOSK_MODEL_PATH` environment variable.

## 4. Launching the System

### Step 1: Start the Backend Server
From the repository root:
```bash
PYTHONPATH=backend ./backend/.venv/bin/python -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000
```
Verify the health endpoint:
```bash
curl http://127.0.0.1:8000/health
```

### Step 2: Compile the VS Code Extension
```bash
cd vscode-extension
npm run compile
```

### Step 3: Launch VS Code Extension Development Host
Launch in an isolated test profile:
```bash
/usr/bin/code --user-data-dir /tmp/ai-code-review-vscode-profile \
  --new-window \
  --extensionDevelopmentPath=/home/shv/Documents/01_sem5/Ai/AI-project/ai-code-review-assistant/vscode-extension \
  /home/shv/Documents/01_sem5/Ai/AI-project/ai-code-review-assistant
```

## 5. Voice Operation

1. Open the Voice Panel by running `AI Review: Voice Assistant` (`aiReview.openVoicePanel`) in the Command Palette (`Ctrl+Shift+P`).
2. **Start Listening:** Click the **`⏺ Start Recording`** button. The status badge will show `🎙 Recording from Microphone…`.
3. **Speak Command:** Speak clearly into your microphone (e.g., *"review this file"*).
4. **Stop Listening:** Click **`⏹ Stop Recording`**. The audio is sent locally to `POST /voice/transcribe`, transcribed in ~100-300ms by Vosk, and automatically dispatched to the corresponding security review command.

### Example Supported Voice Commands:
- `"review this file"` — Runs full security scan on active editor file
- `"show critical findings"` — Filters results to critical / high severity issues
- `"explain finding 1"` — Generates deep XAI explanation for finding #1
- `"generate a fix for finding 1"` — Computes automated remediation diff
- `"accept finding 1"` — Records developer acceptance feedback in RL database
- `"reject finding 1"` — Marks false positive and updates adaptive ranking

### Fallback Command Simulator:
If a physical microphone is not connected or in noisy environments, use the built-in **Fallback Command Simulator** buttons in the voice panel to test command dispatch deterministically without audio recording.

## 6. Troubleshooting

- **"Vosk model directory not found" (HTTP 503):** Run `python backend/download_vosk_model.py` to download the offline acoustic model.
- **"Neither parecord nor arecord is installed":** Run `sudo pacman -S pipewire-pulse` or `sudo pacman -S alsa-utils`.
- **"No audio was captured":** Check KDE Plasma Audio Settings (`System Settings -> Audio -> Recording / Input Devices`) to ensure your microphone is unmuted and the default input device is set.
