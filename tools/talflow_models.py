#!/usr/bin/env python3
"""Benchmark transcription models against one recording.

    tools/talflow_models.py sample.wav [--expect "the true transcript"]
    tools/talflow_models.py sample.wav --models a/b,c/d

Reports word error rate (when a reference is given), wall-clock latency and
the cost the provider actually charged, so the choice of model is made on
this machine's audio rather than on a leaderboard. Latency matters more here
than in batch transcription: it is the pause between letting go of the key
and the words appearing.

Costs real money -- a few hundredths of a penny per model per run.
"""
import argparse
import os
import re
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi                                             # noqa: E402
gi.require_version("Gtk", "3.0")

from taldock.talflow import Settings, transcribe      # noqa: E402

CANDIDATES = [
    "nvidia/parakeet-tdt-0.6b-v3",
    "openai/whisper-large-v3-turbo",
    "openai/whisper-large-v3",
    "openai/gpt-4o-transcribe",
    "openai/gpt-4o-mini-transcribe",
    "openai/gpt-transcribe",
    "deepgram/nova-3",
    "mistralai/voxtral-mini-transcribe",
    "mistralai/voxtral-small-24b-2507-stt",
    "qwen/qwen3-asr-flash-2026-02-10",
    "fish-audio/transcribe-1",
    "google/chirp-3",
    "microsoft/mai-transcribe-2",
    "x-ai/grok-stt-1.0",
]


def normalise(text):
    """Lowercase, strip punctuation: compare words, not comma placement."""
    return re.sub(r"[^a-z0-9' ]", " ", text.lower()).split()


def _distance(ref, hyp):
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        current = [i]
        for j, h in enumerate(hyp, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (r != h)))
        previous = current
    return previous[-1]


def char_error_rate(reference, hypothesis):
    """Levenshtein over characters, keeping case and punctuation.

    WER deliberately throws both away, but a transcript that is typed
    straight into an editor lives or dies by them.
    """
    ref = " ".join(reference.split())
    hyp = " ".join((hypothesis or "").split())
    if not ref:
        return None
    return _distance(ref, hyp) / len(ref)


def word_error_rate(reference, hypothesis):
    """Levenshtein distance over words, divided by reference length."""
    ref, hyp = normalise(reference), normalise(hypothesis)
    if not ref:
        return None
    return _distance(ref, hyp) / len(ref)


def record(path, seconds=None):
    """Capture a sample the same way talflow does, so it is representative."""
    import array
    import math
    import signal
    import subprocess

    for count in (3, 2, 1):
        print(f"  starting in {count}...", end="\r", flush=True)
        time.sleep(1)
    print("  RECORDING -- read the passage aloud." + " " * 20)
    proc = subprocess.Popen(
        ["pw-record", "--rate=16000", "--channels=1", "--format=s16", path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if seconds:
        time.sleep(seconds)
    else:
        input("  press Enter when you have finished reading...")
    proc.send_signal(signal.SIGTERM)
    proc.wait()
    with wave.open(path) as handle:
        frames = handle.readframes(handle.getnframes())
        rate = handle.getframerate()
    samples = array.array("h")
    samples.frombytes(frames)
    level = math.sqrt(sum(v * v for v in samples) / len(samples)) if samples else 0
    print(f"  saved {path}: {len(samples)/rate:.1f}s, RMS {level:.0f}")
    if level < 60:
        print("  WARNING: that is near-silence. Is the microphone muted?")
    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", nargs="?",
                        help="a WAV to test; omit with --record")
    parser.add_argument("--record", action="store_true",
                        help="record from the default microphone, until Enter")
    parser.add_argument("--seconds", type=float, default=None,
                        help="with --record, stop after this long instead")
    parser.add_argument("--expect", default=None,
                        help="reference transcript, to score accuracy")
    parser.add_argument("--expect-file", default=None,
                        help="file holding the reference transcript")
    parser.add_argument("--models", default=None, help="comma-separated list")
    args = parser.parse_args()

    models = args.models.split(",") if args.models else CANDIDATES
    if args.record:
        args.audio = args.audio or "talflow-sample.wav"
        record(args.audio, args.seconds)
    if not args.audio:
        sys.exit("talflow_models: give a WAV, or --record SECONDS")
    if args.expect_file:
        args.expect = open(args.expect_file, encoding="utf-8").read()
    settings = Settings()
    if not settings.api_key:
        sys.exit("talflow_models: no API key configured")
    with wave.open(args.audio) as handle:
        seconds = handle.getnframes() / float(handle.getframerate())
    print(f"{args.audio}: {seconds:.1f}s of audio, {len(models)} models\n")

    rows = []
    for model in models:
        settings["model"] = model
        usage = {}
        started = time.monotonic()
        text, error = transcribe(args.audio, settings, usage=usage)
        elapsed = time.monotonic() - started
        if error:
            print(f"  {model:42} FAILED  {error[:70]}")
            rows.append((model, None, elapsed, None, error, 0.0, None))
            continue
        wer = word_error_rate(args.expect, text) if args.expect else None
        cer = char_error_rate(args.expect, text) if args.expect else None
        cost = float(usage.get("cost") or 0.0)
        rows.append((model, text.strip(), elapsed, wer, None, cost, cer))
        score = (f"WER {wer*100:5.1f}%  CER {cer*100:5.1f}%"
                 if wer is not None else " " * 24)
        print(f"  {model:38} {score}  {elapsed:5.2f}s  "
              f"${cost/seconds*3600:6.3f}/hr")

    print("\n--- transcripts ---")
    for model, text, _e, _w, error, _c, _cer in rows:
        print(f"\n{model}\n  {error or text!r}")

    ok = [r for r in rows if r[1] is not None]
    if args.expect and ok:
        print("\n--- ranked by accuracy, then latency ---")
        for model, _t, elapsed, wer, _e, cost, cer in sorted(
                ok, key=lambda r: (r[3] if r[3] is not None else 9, r[2])):
            print(f"  WER {(wer or 0)*100:5.1f}%  CER {(cer or 0)*100:5.1f}%  "
                  f"{elapsed:5.2f}s  ${cost/seconds*3600:6.3f}/hr  {model}")
        print(f"\n  total spent on this run: "
              f"${sum(r[5] for r in rows):.4f}")


if __name__ == "__main__":
    main()
