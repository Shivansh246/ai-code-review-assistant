#!/usr/bin/env python3
"""
download_vosk_model.py
One-time setup script to download and validate a lightweight English Vosk model
for CPU-only, local, offline speech recognition.

Source: https://alphacephei.com/vosk/models
Default Model: vosk-model-small-en-us-0.15 (~40 MB download, ~100 MB unpacked)
"""

import argparse
import io
import os
import sys
import urllib.request
import zipfile

DEFAULT_MODEL_NAME = "vosk-model-small-en-us-0.15"
MODEL_BASE_URL = "https://alphacephei.com/vosk/models"


def get_default_model_dir() -> str:
    env_path = os.getenv("VOSK_MODEL_PATH")
    if env_path and env_path.strip():
        return os.path.abspath(env_path.strip())
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, "models", DEFAULT_MODEL_NAME)


def validate_model_dir(model_dir: str) -> bool:
    """Verifies that the target directory contains requisite Vosk acoustic model files."""
    if not os.path.isdir(model_dir):
        return False
    
    required_indicators = [
        os.path.join(model_dir, "am", "final.mdl"),
        os.path.join(model_dir, "graph", "HCLr.fst"),
    ]
    return any(os.path.exists(p) for p in required_indicators) or (
        os.path.exists(os.path.join(model_dir, "conf", "model.conf"))
    )


def download_and_extract_model(target_dir: str, model_name: str = DEFAULT_MODEL_NAME) -> None:
    """Downloads model zip from official Vosk repository and unpacks directly to target directory."""
    if validate_model_dir(target_dir):
        print(f"✅ Vosk model is already downloaded and valid at: {target_dir}")
        return

    url = f"{MODEL_BASE_URL}/{model_name}.zip"
    print(f"📦 Downloading Vosk offline model: '{model_name}'...")
    print(f"   URL: {url}")
    print(f"   Destination: {target_dir}")

    parent_dir = os.path.dirname(os.path.abspath(target_dir))
    os.makedirs(parent_dir, exist_ok=True)

    headers = {"User-Agent": "AI-Code-Review-Assistant/1.0"}
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            total_size = int(resp.headers.get("content-length", 0))
            downloaded = 0
            chunks = []
            block_size = 1024 * 64

            while True:
                chunk = resp.read(block_size)
                if not chunk:
                    break
                chunks.append(chunk)
                downloaded += len(chunk)
                if total_size > 0:
                    percent = (downloaded / total_size) * 100
                    mb = downloaded / (1024 * 1024)
                    total_mb = total_size / (1024 * 1024)
                    print(f"\r   Progress: {mb:.1f}MB / {total_mb:.1f}MB ({percent:.1f}%)", end="", flush=True)
                else:
                    mb = downloaded / (1024 * 1024)
                    print(f"\r   Downloaded: {mb:.1f}MB", end="", flush=True)

            print()
            zip_bytes = b"".join(chunks)

        print("📂 Extracting model archive...")
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            zf.extractall(parent_dir)

        extracted_folder = os.path.join(parent_dir, model_name)
        if extracted_folder != target_dir and os.path.exists(extracted_folder):
            if os.path.exists(target_dir):
                import shutil
                shutil.rmtree(target_dir)
            os.rename(extracted_folder, target_dir)

        if validate_model_dir(target_dir):
            print(f"✅ Successfully installed and verified Vosk model at: {target_dir}")
        else:
            print(f"⚠️ Warning: Model extracted to {target_dir}, but some standard model files were not found.")
    except Exception as e:
        print(f"❌ Failed to download or unpack Vosk model: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Download and validate local Vosk speech recognition model.")
    parser.add_argument(
        "--target-dir",
        default=get_default_model_dir(),
        help="Target directory where model should be stored (default: project backend/models/vosk-model-small-en-us-0.15)",
    )
    parser.add_argument(
        "--model-name",
        default=DEFAULT_MODEL_NAME,
        help="Vosk model name from https://alphacephei.com/vosk/models (default: vosk-model-small-en-us-0.15)",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check if model is present and valid without downloading.",
    )
    args = parser.parse_args()

    if args.check_only:
        if validate_model_dir(args.target_dir):
            print(f"✅ Vosk model is valid at: {args.target_dir}")
            sys.exit(0)
        else:
            print(f"❌ Vosk model is NOT found or invalid at: {args.target_dir}")
            sys.exit(1)

    download_and_extract_model(args.target_dir, args.model_name)


if __name__ == "__main__":
    main()
