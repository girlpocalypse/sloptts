# slopvoice

Local Kokoro TTS: a server (`server/`), the `kokoro-speak` client (`bin/`), a Speech
Dispatcher module (`speechd/sd_kokoro`), an Omarchy installer (`omarchy/install.sh`), and
the record of the original Fedora Silverblue 44 setup (`fedora/`). See README.md for the
layout, where each file is installed, and lessons learned.

The files here are **copies**. The live versions are at the paths listed in README.md.
Don't change the running system unless asked; edit the live file and copy it back
here (or the reverse) when asked.

- **Omarchy:** `omarchy/install.sh` installs everything. The server runs in rootless
  Docker only when a container can use the GPU (checked at install time); otherwise it
  runs natively from a uv venv in `~/.local/share/kokoro-tts/.venv`. Keys go in a marked
  block in `~/.config/hypr/bindings.lua`.
- **Fedora:** use `/usr/bin/python3` on the host, since `python3` on PATH is Homebrew's
  and lacks `gi` and `speechd`. The server runs in the `kokoro` distrobox (Python 3.12
  venv in `~/kokoro/.venv`).
- The socket is `$XDG_RUNTIME_DIR/kokoro-tts/kokoro-tts.sock` (it was
  `$XDG_RUNTIME_DIR/kokoro-tts.sock` on Fedora before the Omarchy port). Keep
  `server.py`, `sd_kokoro` and `kokoro-speak` in step.
- Test the installer with stubbed commands and a fake `HOME`; never run it against the
  real `HOME` of whatever machine you're on.

## TODO

- [ ] **Stray words spoken aloud: "espeak" and "spd-say".** Sometimes the TTS speaks
  these words by themselves. Not investigated yet. Ideas:
  - Speech Dispatcher's fallback modules: with espeak-ng commented out, speechd still
    loads `espeak-ng-fallback` and `sd_dummy` (the dummy module plays a canned "speech
    not working" message). Check `$XDG_RUNTIME_DIR/speech-dispatcher/log/` for when
    those modules are used.
  - Orca or another client reading out a client or application name (speechd clients
    register names like "spd-say"), e.g. on focus or notification events.
  - Text reaching `sd_kokoro` that isn't what the app meant to say: log the text of each
    request in `server.py` to catch what triggers it: set `KOKORO_LOG_TEXT=1` (Omarchy: in `~/.config/kokoro-tts/env`).
- [ ] **Reading mode on a second site plays no audio.** After playing audio in Reading
  mode on one site, Reading mode on a different site later won't play audio until
  Chrome is quit and reopened.
- [ ] **Speed up sentence-by-sentence speech.** Going one sentence at a time is slow
  because each sentence pays a start-up cost, so there are gaps between sentences.
  (Speech Dispatcher path only: `kokoro-speak` sends the whole text in one request into
  one `pw-play`, so it has no gaps.)
  Where the time likely goes:
  - Chrome's Reading mode (and Orca) send roughly one sentence per SPEAK, so each
    message starts from scratch in `sd_kokoro`: a new `Job`, a new `pw-play` process
    (PipeWire stream setup), and a new socket request to the server. There's no
    look-ahead across messages; prefetch only works within one message.
  - Within one message, `sd_kokoro` also makes a separate server request per sentence
    (split at `__spd_N` marks), and each one runs its own kokoro-onnx `create_stream`.
  - Ideas: keep one long-lived `pw-play` (or a PipeWire stream) open across messages
    instead of spawning one per job; keep the server connection warm; send a whole message
    in one request and have the server report sentence boundaries back for index
    marks; measure first-audio latency per message before and after (log timestamps in
    `sd_kokoro` and `server.py`). The GPU (see README) would only shave synthesis time,
    not process start-up.
- [ ] **Fix punctuation pronunciation.** Partly done: `server.py`'s `normalize()` now
  spells out decimals, dotted numbers, `v1.2` versions, `name.ext`/domains, "e.g." and
  "i.e.". Tested with kokoro-onnx 0.6.1: `"Opus 5.5."` (decimal followed by more punctuation)
  phonemized as `fˈaɪv. fˈaɪv` without it, while `"Opus 5.5"` alone was already
  "five point five" there. **The user reports it also happens with only one period**, so
  something before the server is probably involved too (Speech Dispatcher symbol
  handling, the Fedora box's older kokoro-onnx, or Chrome's own splitting). Check with
  `KOKORO_LOG_TEXT=1` which text actually arrives. The notes below are from before this. Symptom: punctuation inside words and numbers is
  dropped instead of read. "Opus 5.5" is spoken as "Opus five five", where it should be
  "Opus five point five" or "five dot five". Something between the app and Kokoro
  seems to turn the `.` into a pause or space. Suspects: Speech Dispatcher's
  punctuation/symbol preprocessing with the client's punctuation level; my cleanup
  steps; and the server log, which currently logs only a character count, not the text.
  **Working hypothesis (from the user):** every period is treated as the end of a
  sentence, even inside "5.5", so "5.5" becomes "5. 5" and is read with a sentence break
  and no "point". Where that could happen:
  - kokoro-onnx phonemizes with espeak-ng while keeping punctuation. The punctuation
    handling (phonemizer-style) splits the text at every punctuation mark regardless of
    context, before espeak ever sees "5.5" as a number. espeak-ng on its own normally
    says "five point five".
  - kokoro-onnx's `_prepare` batching then splits at that `.` too
    (`sentence_pause`/`clause_pause`).
  - Probably not Speech Dispatcher's sentence marks: `insert_index_marks` only adds
    `__spd_N` after `.`/`?`/`!` followed by whitespace, `<` or `&`, so not inside "5.5".
  - Possible fix: normalize text before phonemizing (e.g. `5.5` to "5 point 5", or
    protect decimals, version numbers, abbreviations and URLs) in `server.py`'s
    `clean()`, or check whether kokoro-onnx has an option for this.
  Places to look:
  - Speech Dispatcher's own symbol handling: the server can replace punctuation with
    words (`symbols.dic`, via `insert_symbols`) depending on the client's
    `punctuation_mode` before text reaches the module. `sd_kokoro` currently ignores
    the `punctuation_mode` setting.
  - `sd_kokoro`'s `segments()`: SSML stripping and `html.unescape`.
  - `server.py`'s `clean()`, and how kokoro-onnx/espeak-ng phonemize symbols.
  - Test with `spd-say -m none|some|most|all "..."` and watch
    `journalctl --user -u kokoro-tts -f` to see the text that actually reaches Kokoro.
