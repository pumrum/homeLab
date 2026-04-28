#!/usr/bin/env python3
"""
Audio Transcription Script with Speaker Identification
=========================================================
Transcribes audio files with timestamps and speaker labels.

This script creates its own virtual environment with pinned, compatible
dependency versions.

Supports: m4a, mp3, mp4, ogg, flac, wav, webm, aac, wma
(Non-wav formats are auto-converted via ffmpeg.)

You'll need a free HuggingFace token for speaker diarization:
  1. Create an account at https://huggingface.co
  2. Accept the terms at https://huggingface.co/pyannote/speaker-diarization-3.1
  3. Accept the terms at https://huggingface.co/pyannote/segmentation-3.0
  4. Create a token at https://huggingface.co/settings/tokens

Usage:
  python3 transcribe_audio.py "audio.m4a" --hf-token hf_XXXX
  python3 transcribe_audio.py "audio.m4a" --whisper-model medium --language en
  python3 transcribe_audio.py "audio.m4a" --no-diarize
"""

import argparse
import os
import subprocess
import sys
import textwrap
from pathlib import Path

VENV_DIR = Path(__file__).resolve().parent / ".transcribe_venv"

# ── Virtual environment bootstrap ─────────────────────────────────────────────

def in_venv():
    return sys.prefix != sys.base_prefix

def bootstrap_venv():
    """Create a clean venv with pinned deps and re-launch this script inside it."""
    if in_venv():
        return

    venv_python = VENV_DIR / "bin" / "python3"

    # Check for --clean-venv flag before arg parsing (we're outside venv)
    if "--clean-venv" in sys.argv:
        import shutil
        if VENV_DIR.exists():
            print(f"Removing old venv at {VENV_DIR} ...")
            shutil.rmtree(VENV_DIR)
        sys.argv.remove("--clean-venv")

    if not venv_python.exists():
        print(f"Creating virtual environment at {VENV_DIR} ...")
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])

        print("Installing dependencies (this takes a few minutes the first time)...\n")
        pip = [str(venv_python), "-m", "pip"]

        subprocess.check_call(pip + ["install", "--upgrade", "pip"],
                              stdout=subprocess.DEVNULL)

        print("  Installing PyTorch, Whisper, pyannote, and all dependencies...")
        subprocess.check_call(pip + [
            "install",
            "torch==2.5.1",
            "torchaudio==2.5.1",
            "numpy<2",
            "openai-whisper",
            "pyannote.audio==3.3.2",
            "matplotlib",
        ])

        print("\nDependencies installed.\n")

    # Re-exec this same script inside the venv
    os.execv(str(venv_python), [str(venv_python), __file__] + sys.argv[1:])


bootstrap_venv()

# ── From here on we're running inside the venv ────────────────────────────────

# ── Monkey-patch huggingface_hub BEFORE any pyannote imports ──────────────────
# pyannote.audio 3.3.2 passes use_auth_token= to hf_hub_download(), but newer
# versions of huggingface_hub removed that kwarg in favor of token=.

import huggingface_hub
import functools

def _patch_hf_func(original_func):
    @functools.wraps(original_func)
    def wrapper(*args, **kwargs):
        if "use_auth_token" in kwargs:
            kwargs["token"] = kwargs.pop("use_auth_token")
        return original_func(*args, **kwargs)
    return wrapper

for fn_name in ("hf_hub_download", "snapshot_download", "model_info", "repo_info"):
    if hasattr(huggingface_hub, fn_name):
        setattr(huggingface_hub, fn_name, _patch_hf_func(getattr(huggingface_hub, fn_name)))

# Monkey-patch torchaudio in case AudioMetaData was removed
import torchaudio
if not hasattr(torchaudio, "AudioMetaData"):
    torchaudio.AudioMetaData = object
if not hasattr(torchaudio, "list_audio_backends"):
    torchaudio.list_audio_backends = lambda: ["sox_io"]

import warnings
warnings.filterwarnings("ignore", message=".*weights_only.*")
warnings.filterwarnings("ignore", message=".*FP16 is not supported on CPU.*")

os.environ["OMP_NUM_THREADS"] = os.environ.get("OMP_NUM_THREADS", "1")
os.environ["KMP_WARNINGS"] = "0"

import json
import whisper
import torch
import numpy as np
import tempfile


# ── Audio conversion ──────────────────────────────────────────────────────────

def ensure_wav(audio_path: str) -> str:
    """
    If the file is not a .wav, convert it to 16kHz mono WAV using ffmpeg.
    Returns the path to use (original if already wav, temp file otherwise).
    """
    p = Path(audio_path)
    if p.suffix.lower() == ".wav":
        return audio_path

    # Check ffmpeg is available
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("ERROR: ffmpeg is required to convert non-wav audio files.")
        print("Install it with:  brew install ffmpeg")
        sys.exit(1)

    wav_path = p.with_suffix(".wav")
    if wav_path.exists():
        print(f"Using existing WAV file: {wav_path}")
        return str(wav_path)

    print(f"Converting {p.name} to WAV (16kHz mono) for compatibility...")
    subprocess.check_call([
        "ffmpeg", "-i", str(audio_path),
        "-ar", "16000",      # 16kHz sample rate (optimal for speech models)
        "-ac", "1",           # mono
        "-c:a", "pcm_s16le",  # 16-bit PCM
        "-y",                 # overwrite if exists
        str(wav_path),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    size_mb = wav_path.stat().st_size / (1024 * 1024)
    print(f"  Created: {wav_path.name} ({size_mb:.0f} MB)\n")
    return str(wav_path)


# ── Helpers ───────────────────────────────────────────────────────────────────

def fmt_time(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def load_diarization_pipeline(hf_token: str):
    from pyannote.audio import Pipeline

    print("Loading speaker diarization model (first run downloads ~1 GB)...")
    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=hf_token,
    )
    if torch.cuda.is_available():
        pipeline.to(torch.device("cuda"))
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        pipeline.to(torch.device("mps"))
    return pipeline


def run_diarization(pipeline, audio_path: str):
    print("Running speaker diarization (this takes a while on long files)...")
    diarization = pipeline(audio_path)
    segments = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        segments.append((turn.start, turn.end, speaker))
    n_speakers = len(set(s[2] for s in segments))
    print(f"  Found {n_speakers} speaker(s).\n")
    return segments


def run_whisper(audio_path: str, model_size: str, language):
    device = "cpu"
    if torch.cuda.is_available():
        device = "cuda"
    print(f"Loading Whisper '{model_size}' model on {device}...")
    model = whisper.load_model(model_size, device=device)

    print("Transcribing audio (this is the slow part — be patient)...")
    options = dict(verbose=False, word_timestamps=True)
    if language:
        options["language"] = language
    result = model.transcribe(audio_path, **options)
    print(f"  Transcribed {len(result['segments'])} segments.\n")
    return result["segments"]


def assign_speakers(whisper_segments, diarization_segments):
    def best_speaker(seg_start, seg_end):
        overlaps = {}
        for d_start, d_end, speaker in diarization_segments:
            overlap_start = max(seg_start, d_start)
            overlap_end = min(seg_end, d_end)
            if overlap_end > overlap_start:
                overlaps[speaker] = overlaps.get(speaker, 0) + (overlap_end - overlap_start)
        if overlaps:
            return max(overlaps, key=overlaps.get)
        return "UNKNOWN"

    results = []
    for seg in whisper_segments:
        speaker = best_speaker(seg["start"], seg["end"])
        results.append({
            "start":   seg["start"],
            "end":     seg["end"],
            "speaker": speaker,
            "text":    seg["text"].strip(),
        })
    return results


def merge_consecutive(segments):
    if not segments:
        return segments
    merged = [segments[0].copy()]
    for seg in segments[1:]:
        prev = merged[-1]
        if seg["speaker"] == prev["speaker"] and (seg["start"] - prev["end"]) < 1.5:
            prev["end"] = seg["end"]
            prev["text"] += " " + seg["text"]
        else:
            merged.append(seg.copy())
    return merged


def rename_speakers(segments):
    seen = {}
    counter = 1
    for seg in segments:
        raw = seg["speaker"]
        if raw not in seen and raw != "UNKNOWN":
            seen[raw] = f"Speaker {counter}"
            counter += 1
    for seg in segments:
        seg["speaker"] = seen.get(seg["speaker"], seg["speaker"])
    return segments


# ── Output formatters ─────────────────────────────────────────────────────────

def write_txt(segments, path):
    with open(path, "w") as f:
        current_speaker = None
        for seg in segments:
            if seg["speaker"] != current_speaker:
                current_speaker = seg["speaker"]
                f.write(f"\n{current_speaker}\n")
            f.write(f"  [{fmt_time(seg['start'])} - {fmt_time(seg['end'])}]  {seg['text']}\n")
    print(f"  Saved: {path}")


def write_json(segments, path):
    with open(path, "w") as f:
        json.dump(segments, f, indent=2, ensure_ascii=False)
    print(f"  Saved: {path}")


def write_srt(segments, path):
    with open(path, "w") as f:
        for i, seg in enumerate(segments, 1):
            start = fmt_srt_time(seg["start"])
            end = fmt_srt_time(seg["end"])
            f.write(f"{i}\n{start} --> {end}\n[{seg['speaker']}] {seg['text']}\n\n")
    print(f"  Saved: {path}")


def fmt_srt_time(seconds):
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Transcribe audio with speaker identification and timestamps.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python3 transcribe_audio.py audio.m4a --hf-token hf_XXXX
              python3 transcribe_audio.py audio.m4a --whisper-model medium --language en
              python3 transcribe_audio.py audio.m4a --no-diarize

            Troubleshooting:
              If you hit dependency errors, try:  --clean-venv
              This deletes and recreates the virtual environment.
        """),
    )
    parser.add_argument("audio", help="Path to audio file (m4a, mp3, wav, etc.)")
    parser.add_argument("--hf-token", default=os.environ.get("HF_TOKEN"),
                        help="HuggingFace token (or set HF_TOKEN env var)")
    parser.add_argument("--whisper-model", default="medium",
                        choices=["tiny", "base", "small", "medium", "large"],
                        help="Whisper model size (default: medium)")
    parser.add_argument("--language", default=None,
                        help="Language code, e.g. 'en' (auto-detected if omitted)")
    parser.add_argument("--no-diarize", action="store_true",
                        help="Skip speaker diarization (just transcribe)")
    parser.add_argument("--output-dir", default=None,
                        help="Output directory (default: same as input file)")
    args = parser.parse_args()

    audio_path = Path(args.audio).resolve()
    if not audio_path.exists():
        sys.exit(f"Error: file not found: {audio_path}")

    out_dir = Path(args.output_dir) if args.output_dir else audio_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # Use the original filename (without .wav) for output file naming
    stem = audio_path.stem

    # Convert to WAV if needed (m4a, mp3, etc. are not readable by soundfile)
    wav_path = ensure_wav(str(audio_path))

    # ── Step 1: Whisper transcription ──
    whisper_segments = run_whisper(wav_path, args.whisper_model, args.language)

    # ── Step 2: Speaker diarization (optional) ──
    if args.no_diarize:
        final = [{"start": s["start"], "end": s["end"], "speaker": "Speaker", "text": s["text"].strip()}
                 for s in whisper_segments]
    else:
        if not args.hf_token:
            print("=" * 60)
            print("ERROR: Speaker diarization requires a HuggingFace token.")
            print()
            print("  1. Create a free account: https://huggingface.co")
            print("  2. Accept model terms:")
            print("     https://huggingface.co/pyannote/speaker-diarization-3.1")
            print("     https://huggingface.co/pyannote/segmentation-3.0")
            print("  3. Get a token: https://huggingface.co/settings/tokens")
            print("  4. Run again with:  --hf-token hf_YOUR_TOKEN")
            print("     Or set:          export HF_TOKEN=hf_YOUR_TOKEN")
            print()
            print("  To skip speaker ID, add:  --no-diarize")
            print("=" * 60)
            sys.exit(1)

        pipeline = load_diarization_pipeline(args.hf_token)
        diar_segments = run_diarization(pipeline, wav_path)
        final = assign_speakers(whisper_segments, diar_segments)

    # ── Step 3: Clean up ──
    final = merge_consecutive(final)
    final = rename_speakers(final)

    # ── Step 4: Write outputs ──
    print("Writing output files...")
    write_txt(final, out_dir / f"{stem}_transcript.txt")
    write_json(final, out_dir / f"{stem}_transcript.json")
    write_srt(final, out_dir / f"{stem}_transcript.srt")

    # ── Print preview ──
    print(f"\n{'─' * 60}")
    print(f"TRANSCRIPT PREVIEW (first 20 entries)")
    print(f"{'─' * 60}\n")
    current_speaker = None
    for seg in final[:20]:
        if seg["speaker"] != current_speaker:
            current_speaker = seg["speaker"]
            print(f"\n  {current_speaker}")
        print(f"    [{fmt_time(seg['start'])} - {fmt_time(seg['end'])}]  {seg['text']}")
    if len(final) > 20:
        print(f"\n  ... and {len(final) - 20} more segments (see full files)")
    print()


if __name__ == "__main__":
    main()
