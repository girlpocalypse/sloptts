"""Kokoro TTS server: keeps the model loaded and streams speech over a Unix socket.

Protocol: the client sends one JSON line {"text", "voice", "speed"} and reads
raw mono float32 PCM at 24 kHz until the server closes the connection. If the
client disconnects (Speech Dispatcher stopping speech), synthesis is abandoned.

Environment:
    KOKORO_SOCKET     socket path (default $XDG_RUNTIME_DIR/kokoro-tts/kokoro-tts.sock)
    KOKORO_MODEL_DIR  directory with kokoro-v1.0.onnx and voices-v1.0.bin (default: next to this file)
    KOKORO_LOG_TEXT   set to 1 to log the text of each request (off: the journal would keep everything spoken)
"""
import asyncio
import json
import logging
import os
import re
from pathlib import Path

import onnxruntime

RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
SOCKET = Path(os.environ.get("KOKORO_SOCKET") or RUNTIME_DIR / "kokoro-tts" / "kokoro-tts.sock")
MODEL_DIR = Path(os.environ.get("KOKORO_MODEL_DIR") or Path(__file__).parent)
LOG_TEXT = os.environ.get("KOKORO_LOG_TEXT") == "1"
DEFAULT_VOICE = "af_heart"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("kokoro-server")

# onnxruntime-gpu from pip finds the CUDA libraries of the nvidia-* wheels only
# once they are preloaded; a no-op without them
if "CUDAExecutionProvider" in onnxruntime.get_available_providers():
    try:
        onnxruntime.preload_dlls()
    except Exception as error:  # an older onnxruntime, or no CUDA wheels
        log.warning("preload_dlls failed: %s", error)

from kokoro_onnx import Kokoro  # noqa: E402  (after the CUDA preload)

kokoro = Kokoro(str(MODEL_DIR / "kokoro-v1.0.onnx"), str(MODEL_DIR / "voices-v1.0.bin"))
voices = set(kokoro.get_voices())

# espeak-ng (through phonemizer, which keeps punctuation) treats a "." inside a
# token as a sentence end when more punctuation follows: "Opus 5.5." comes out
# as "five. five", and kokoro-onnx then pauses at that ".". Spelling these out
# keeps the dot from reaching the phonemizer.
VERSION_RE = re.compile(r"\b[vV](?=\d+(?:\.\d+)+\b)")  # v2.0 -> v 2.0
DOTTED_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+){2,}(?![\w]|\.\d)")  # 1.2.3, 192.168.0.1
DECIMAL_RE = re.compile(r"(?<![\w.])(\d+)\.(\d+)(?![\w]|\.\d)")  # 5.5, 0.27, 1,000.50
DOMAIN_RE = re.compile(r"(?<=[A-Za-z0-9])\.(?=[a-z]{2,}\b)")  # example.com, server.py
ABBREVIATIONS = {"e.g.": "for example", "i.e.": "that is"}


def normalize(text: str) -> str:
    """Spell out dots inside numbers, versions and names so they aren't read as sentence ends."""
    for short, long in ABBREVIATIONS.items():
        text = re.sub(rf"(?<!\w){re.escape(short)}", long, text, flags=re.IGNORECASE)
    text = VERSION_RE.sub(lambda m: m.group() + " ", text)
    text = DOTTED_RE.sub(lambda m: " dot ".join(m.group().split(".")), text)
    # Digits after the point are read one by one, as espeak does for "5.25"
    text = DECIMAL_RE.sub(lambda m: f"{m[1]} point {' '.join(m[2])}", text)
    return DOMAIN_RE.sub(" dot ", text)


def clean(text: str) -> str:
    """Strip SSML markup Speech Dispatcher may pass through, collapse whitespace, normalize."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    text = text.replace("&apos;", "'").replace("&amp;", "&")
    return normalize(" ".join(text.split()))


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request = json.loads(await reader.readline())
        text = clean(request.get("text", ""))
        voice = request.get("voice") or DEFAULT_VOICE
        if voice not in voices:
            voice = DEFAULT_VOICE
        speed = min(max(float(request.get("speed", 1.0)), 0.5), 2.0)
        lang = "en-gb" if voice.startswith("b") else "en-us"
        if not text:
            return
        log.info("speak voice=%s speed=%.2f chars=%d", voice, speed, len(text))
        if LOG_TEXT:
            log.info("text: %r", text)
        async for samples, _rate in kokoro.create_stream(text, voice=voice, speed=speed, lang=lang):
            writer.write(samples.astype("float32").tobytes())
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError):
        log.info("client disconnected, speech stopped")
    except Exception:
        log.exception("request failed")
    finally:
        writer.close()


async def main() -> None:
    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    SOCKET.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(handle, path=str(SOCKET))
    SOCKET.chmod(0o600)
    providers = kokoro.sess.get_providers()
    log.info("providers: %s", ", ".join(providers))
    wanted = os.environ.get("ONNX_PROVIDER")
    if wanted and wanted not in providers:
        log.warning("%s was requested but isn't active: running on the CPU", wanted)
    log.info("listening on %s", SOCKET)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
