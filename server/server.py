"""Kokoro TTS server: keeps the model loaded and streams speech over a Unix socket.

Protocol: the client sends one JSON line {"text", "voice", "speed"} and reads
raw mono float32 PCM at 24 kHz until the server closes the connection. If the
client disconnects (Speech Dispatcher stopping speech), synthesis is abandoned.

Environment:
    KOKORO_SOCKET     socket path (default $XDG_RUNTIME_DIR/kokoro-tts/kokoro-tts.sock)
    KOKORO_MODEL_DIR  directory with kokoro-v1.0.onnx and voices-v1.0.bin (default: next to this file)
    KOKORO_LOG_TEXT   set to 1 to log the text of each request (off: the journal would keep everything spoken)
    ONNX_PROVIDER     onnxruntime execution provider (default: CUDAExecutionProvider when
                      onnxruntime-gpu can use it, otherwise the CPU)
    ORT_DISABLE_TELEMETRY  set to 1 here; set it to 0 to let onnxruntime send usage data
"""
import asyncio
import json
import logging
import os
import re
import unicodedata
from pathlib import Path

# onnxruntime's pip builds send usage telemetry to Microsoft (device ID, OS, CPU, RAM,
# executable path, providers, run counts and timings, errors), queued in
# ~/.cache/Microsoft/DeveloperTools/.onnxruntime/. This must be set before the import.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")

import onnxruntime  # noqa: E402

RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
SOCKET = Path(os.environ.get("KOKORO_SOCKET") or RUNTIME_DIR / "kokoro-tts" / "kokoro-tts.sock")
MODEL_DIR = Path(os.environ.get("KOKORO_MODEL_DIR") or Path(__file__).parent)
LOG_TEXT = os.environ.get("KOKORO_LOG_TEXT") == "1"
DEFAULT_VOICE = "af_heart"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("kokoro-server")

# onnxruntime-gpu from pip finds the CUDA libraries of the nvidia-* wheels only once
# they are preloaded. Ask for CUDA explicitly: kokoro-onnx would otherwise try every
# provider the build has, starting with TensorRT, which isn't installed.
if "CUDAExecutionProvider" in onnxruntime.get_available_providers():
    try:
        onnxruntime.preload_dlls()
        os.environ.setdefault("ONNX_PROVIDER", "CUDAExecutionProvider")
    except Exception as error:  # no CUDA wheels: stay on the CPU
        log.warning("preload_dlls failed: %s", error)

from kokoro_onnx import Kokoro  # noqa: E402  (after the CUDA preload)

kokoro = Kokoro(str(MODEL_DIR / "kokoro-v1.0.onnx"), str(MODEL_DIR / "voices-v1.0.bin"))
voices = set(kokoro.get_voices())
# The first run on CUDA sets up kernels and memory (~0.6 s); pay that now, not on the first request
kokoro.create("Ready.", voice=DEFAULT_VOICE)

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


# Rendered math (KaTeX, MathML) uses look-alike letters such as 𝜋 (U+1D70B) and 𝑟,
# and espeak-ng reads letters it has no name for as "letter 1D70B". Folding these
# font variants (and fullwidth, circled and fraction forms) to plain text fixes that.
FOLD_TAGS = ("<font>", "<wide>", "<narrow>", "<circle>", "<fraction>")
KEEP = set("½⅓⅔¼¾")  # fractions espeak-ng reads as "a half" and the like
GREEK_VARIANTS = str.maketrans("ϵϑϖϱϕϰϴ∆", "εθπρφκΘΔ")
DOTTED = str.maketrans({"\u1e8b": "x\u0307", "\u1e8d": "x\u0308", "\u1e8f": "y\u0307", "\u0233": "y\u0304"})  # ẋ ẍ ẏ ȳ


def tagged(tag: str) -> str:
    """Characters below U+2100 whose compatibility decomposition has this tag (skipping ª º)."""
    return "".join(chr(c) for c in range(0xC0, 0x2100) if unicodedata.decomposition(chr(c)).startswith(tag))


SUP, SUB = re.escape(tagged("<super>") + "¹²³"), re.escape(tagged("<sub>"))
SUPERSCRIPT_RE = re.compile(f"[{SUP}]+")
SUBSCRIPT_RE = re.compile(f"[{SUB}]+")
# ∫₀¹, ∑ᵢ₌₁ⁿ: bounds before the generic super- and subscripts
BOUNDS_RE = re.compile(f"([∫∬∭∮∑∏])\\s*([{SUB}]+)([{SUP}]*)")
BOUND_NAMES = {"∫": "integral", "∬": "double integral", "∭": "triple integral",
               "∮": "contour integral", "∑": "sum", "∏": "product"}
CARET_RE = re.compile(r"(?<=[\w)])\^(\{[^{}]*\}|\([^()]*\)|[-−]?\w+)")  # x^2, e^{-x}, e^(x+1)
FUNCTIONS = "sin|cos|tan|sec|csc|cot|sinh|cosh|tanh|[fgh]"
INVERSE_RE = re.compile(rf"\b({FUNCTIONS})(?:⁻¹|\^\{{?[-−]1\}}?)")  # sin⁻¹, f^{-1}
# One-letter operands: dy/dx, ∂f/∂x, d²y/dx², 1/2. Not 10/15/2026, km/h, and/or or A/B.
SIDE = rf"(?:\d+|[d∂]?[{SUP}]*[a-zα-ω][{SUP}]*)"
GROUP = r"\([^()/]{1,20}\)"
SLASH_RE = re.compile(rf"((?<![\w/.]){SIDE}|{GROUP})/({SIDE}|{GROUP})(?![\w/])")
# Accents: combining marks always, spacing ones (as copied from MathML: "xˉ") only after a lone letter
COMBINING = {"̄": "bar", "̅": "bar", "̇": "dot", "̈": "double dot",
             "̂": "hat", "̃": "tilde", "⃗": "vector", "⃑": "vector"}
SPACING = {"¯": "bar", "ˉ": "bar", "‾": "bar", "˙": "dot", "¨": "double dot", "^": "hat",
           "~": "tilde", "→": "vector"}
COMBINING_RE = re.compile(rf"(?<![^\W\d_])([^\W\d_])([{''.join(COMBINING)}])")
SPACING_RE = re.compile(rf"(?<![^\W\d_])([^\W\d_])([{re.escape(''.join(SPACING))}])(?![\w({{∞+\-−])")
NORM_RE = re.compile(r"(?:‖|\|\|)(?=\S)([^|‖\n]{1,30}?)(?<=\S)(?:‖|\|\|)")
ABS_RE = re.compile(r"[|∣](?=\S)([^|∣\n]{1,30}?)(?<=\S)[|∣]")
# 5! = 120, n!, (n+1)!; not "Top 10!" or "Done!"
FACTORIAL_RE = re.compile(r"(?<![\w.])(\d+(?=!\s*[=+\-−×*/·⋅)(])|[b-z]|\((?=[^()]*[\d+\-−])[^()]{1,20}\))!(?!=)")
PRIME_RE = re.compile(r"(?<![\w'])([a-zA-Z])('{1,3})(?=\(|\s*=)")  # f'(x), y'' =
# espeak-ng drops < and > altogether
COMPARE_RE = re.compile(r"(?<=[\w)\]])\s*([<>]=?)\s*(?=[\w(−]|-[\w(])")
COMPARISONS = {"<": "less than", ">": "greater than", "<=": "less than or equal to",
               ">=": "greater than or equal to"}
MIDDLE_DOT_RE = re.compile(r"(?<=[\w)])·(?=[\w(])")  # a·b, but not "Home · About"
GREEK_RE = re.compile(r"(?<=[α-ωΑ-Ω])(?=[A-Za-zα-ωΑ-Ω])|(?<=[A-Za-z])(?=[α-ωΑ-Ω])")
# Symbols espeak-ng skips or misreads (∑, ≠, ≥, ×, ±, ∫, √ and the like it reads itself)
SYMBOLS = {
    "ℏ": "h bar", "ħ": "h bar", "∂": "partial", "∈": "in", "∉": "not in", "∋": "contains",
    "⊂": "subset of", "⊆": "subset of or equal to", "⊊": "proper subset of", "⊃": "superset of",
    "⊇": "superset of or equal to", "⊋": "proper superset of", "∪": "union",
    "∩": "intersection", "∖": "minus", "∅": "the empty set", "∀": "for all",
    "∃": "there exists", "∄": "there is no", "∧": "and", "∨": "or", "⊕": "xor",
    "⊗": "tensor", "⨁": "direct sum", "⨂": "tensor product", "∘": "composed with",
    "∏": "product", "∇": "nabla", "∭": "triple integral", "∮": "contour integral",
    "∯": "surface integral", "∰": "volume integral", "∓": "minus or plus", "⁄": "over",
    "∕": "over", "⋅": "times", "∙": "times", "⨯": "cross", "∼": "tilde",
    "≃": "approximately equal to", "≅": "congruent to", "≔": "is defined as",
    "≜": "is defined as", "≐": "approaches", "≲": "less than or approximately",
    "≳": "greater than or approximately", "≪": "much less than", "≫": "much greater than",
    "→": "to", "⟶": "to", "⟵": "left arrow", "⟷": "left right arrow", "⇒": "implies",
    "⟹": "implies", "⇐": "is implied by", "⟸": "is implied by", "⇔": "if and only if",
    "⟺": "if and only if", "↦": "maps to", "⊥": "perpendicular to", "∥": "parallel to",
    "∦": "not parallel to", "∠": "angle", "∡": "angle", "△": "triangle", "∶": "to",
    "∷": "as", "‰": "per mille", "′": "prime", "″": "double prime", "‴": "triple prime",
    "⋯": "dot dot dot", "⋮": "dot dot dot", "⋱": "dot dot dot", "⌊": "the floor of",
    "⌈": "the ceiling of", "⌋": "", "⌉": "", "✓": "check", "✔": "check", "✗": "cross",
    "✘": "cross",
}
SYMBOLS_RE = re.compile("|".join(map(re.escape, SYMBOLS)))
WORDS = {"sinh": "hyperbolic sine", "cosh": "hyperbolic cosine", "tanh": "hyperbolic tangent",
         "csc": "cosecant", "ln": "natural log", "lim": "limit", "det": "determinant"}
WORDS_RE = re.compile(rf"\b({'|'.join(WORDS)})\b")


def power(exponent: str) -> str:
    """Words for an exponent: squared, cubed, transpose, or "to the power of ..."."""
    exponent = exponent.strip().replace("−", "-")
    if re.fullmatch(r"\d+[-+]", exponent):  # an ion's charge: Fe³⁺
        return f" {exponent[:-1]} {'plus' if exponent[-1] == '+' else 'minus'} "
    words = {"2": "squared", "3": "cubed", "T": "transpose", "+": "plus", "-": "minus"}.get(exponent)
    return f" {words or 'to the power of ' + exponent.replace('-', ' minus ')} "


def plain_symbols(text: str) -> str:
    """Turn math letters, notation and symbols espeak-ng can't read into words."""
    text = fold(text).translate(GREEK_VARIANTS).translate(DOTTED)
    text = INVERSE_RE.sub(r"\1 inverse", text)
    text = COMBINING_RE.sub(lambda m: accent(m[1], COMBINING[m[2]]), text)
    text = SPACING_RE.sub(lambda m: accent(m[1], SPACING[m[2]]), text)
    text = SLASH_RE.sub(lambda m: m.group() if m.group() in ("w/o", "24/7") else f"{m[1]} over {m[2]}", text)
    text = BOUNDS_RE.sub(bounds, text)
    text = SUPERSCRIPT_RE.sub(lambda m: power(unicodedata.normalize("NFKC", m.group())), text)
    text = CARET_RE.sub(lambda m: power(m[1].strip("{}")), text)
    text = SUBSCRIPT_RE.sub(lambda m: f" {unicodedata.normalize('NFKC', m.group())} ", text)
    text = NORM_RE.sub(r" the norm of \1 ", text)
    text = ABS_RE.sub(r" the absolute value of \1 ", text)
    text = FACTORIAL_RE.sub(r"\1 factorial", text)
    text = PRIME_RE.sub(lambda m: f"{m[1]} {['prime', 'double prime', 'triple prime'][len(m[2]) - 1]}", text)
    text = re.sub(r"(?<!\S)<3(?!\d)", "heart", text)  # I <3 this
    text = COMPARE_RE.sub(lambda m: f" {COMPARISONS[m[1]]} ", text)
    text = MIDDLE_DOT_RE.sub(" times ", text)
    text = SYMBOLS_RE.sub(lambda m: f" {SYMBOLS[m.group()]} ", text)
    text = WORDS_RE.sub(lambda m: WORDS[m.group()], text)
    return GREEK_RE.sub(" ", text)  # "πr" and "αβ" are read as one word


def fold(text: str) -> str:
    """Fold math letters to plain ones. Italic ones are variables, so keep them apart from
    other letters: MathML copies "sin𝑥" and "4𝑎𝑐" without spaces, and "𝑑𝐫" reads as "doctor".
    Bold and other styles stay joined: "𝐁𝐨𝐥𝐝" is often just styled text."""
    out, previous = [], ""  # previous: "italic", "letter" or ""
    for c in text:
        if c not in KEEP and unicodedata.decomposition(c).startswith(FOLD_TAGS):
            c, italic = unicodedata.normalize("NFKC", c), unicodedata.name(c, "").startswith("MATHEMATICAL ITALIC")
        else:
            italic = False
        kind = ("italic" if italic else "letter") if c.isalpha() and unicodedata.category(c) != "Lm" else ""
        if kind and previous and "italic" in (kind, previous):
            out.append(" ")
        out.append(c)
        previous = kind
    return "".join(out)


def bounds(match: re.Match) -> str:
    low = unicodedata.normalize("NFKC", match[2])
    high = unicodedata.normalize("NFKC", match[3])
    return f" {BOUND_NAMES[match[1]]} " + (f"from {low} to {high} " if high else f"over {low} ")


def accent(letter: str, mark: str) -> str:
    return f" vector {letter} " if mark == "vector" else f" {letter} {mark} "


SSML_RE = re.compile(r"</?(?:speak|mark|s|p|voice|prosody|break|say-as|emphasis|sub|audio|phoneme|lang)\b[^<>]*>")


def clean(text: str) -> str:
    """Strip SSML markup Speech Dispatcher may pass through, collapse whitespace, normalize."""
    text = SSML_RE.sub(" ", text)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    text = text.replace("&apos;", "'").replace("&amp;", "&")
    return normalize(" ".join(plain_symbols(text).split()))


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
