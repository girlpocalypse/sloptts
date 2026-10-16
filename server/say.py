"""Speak text with Kokoro: say.py VOICE "text" [--speed 1.0] [--out file.wav]"""
import argparse
import os
import subprocess
import tempfile
from pathlib import Path

os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")  # see server.py; before onnxruntime loads

import soundfile as sf  # noqa: E402
from kokoro_onnx import Kokoro  # noqa: E402

HERE = Path(__file__).parent

parser = argparse.ArgumentParser()
parser.add_argument("voice", nargs="?", default="bf_emma")
parser.add_argument("text", nargs="?")
parser.add_argument("--speed", type=float, default=1.0)
parser.add_argument("--out")
parser.add_argument("--list", action="store_true")
args = parser.parse_args()

kokoro = Kokoro(str(HERE / "kokoro-v1.0.onnx"), str(HERE / "voices-v1.0.bin"))

if args.list:
    print("\n".join(sorted(kokoro.get_voices())))
    raise SystemExit

lang = "en-gb" if args.voice.startswith("b") else "en-us"
samples, rate = kokoro.create(args.text, voice=args.voice, speed=args.speed, lang=lang)

if args.out:
    sf.write(args.out, samples, rate)
else:
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        sf.write(f.name, samples, rate)
        subprocess.run(["pw-play", f.name], check=True)
