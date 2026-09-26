"""Kokoro TTS server: keeps the model loaded and streams speech over a Unix socket.

Protocol: the client sends one JSON line {"text", "voice", "speed"} and reads
raw mono float32 PCM at 24 kHz until the server closes the connection. If the
client disconnects (Speech Dispatcher stopping speech), synthesis is abandoned.
"""
import asyncio
import json
import logging
import os
import re
from pathlib import Path

from kokoro_onnx import Kokoro

HERE = Path(__file__).parent
SOCKET = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "kokoro-tts.sock"
DEFAULT_VOICE = "af_heart"

log = logging.getLogger("kokoro-server")
kokoro = Kokoro(str(HERE / "kokoro-v1.0.onnx"), str(HERE / "voices-v1.0.bin"))
voices = set(kokoro.get_voices())


def clean(text: str) -> str:
    """Strip SSML markup Speech Dispatcher may pass through, and collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    text = text.replace("&apos;", "'").replace("&amp;", "&")
    return " ".join(text.split())


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
    SOCKET.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(handle, path=str(SOCKET))
    SOCKET.chmod(0o600)
    log.info("listening on %s", SOCKET)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(main())
