# slopvoice
Note from the human who told claude to make this: THIS IS PURE SLOP. I HAVE NOT READ THE CODE. UPLOADED FOR REFERENCES PURPOSES ONLY. I HIGHLY RECOMMEND YOU DO NOT ATTEMPT TO USE THIS AS-IS.

Local, high-quality text-to-speech with **Kokoro-82M** (voice `af_heart`) through
[kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx). A small server keeps the model
loaded and streams audio over a Unix socket. Clients send it text:

- **`kokoro-speak`**: speak the highlighted text, the clipboard, an argument or stdin. On
  Omarchy it is bound to a key (press again to stop).
- **`sd_kokoro`**: a Speech Dispatcher module, for Chrome's Reading mode "Read aloud",
  `spd-say` and Orca.

It started on Fedora Silverblue (see [Fedora](#fedora-silverblue-the-original-setup)) and
now has an installer for [Omarchy](https://omarchy.org/) (Arch + Hyprland).

## Layout

| Path | What it is |
|---|---|
| `server/server.py` | The TTS server. Protocol: one JSON line `{"text","voice","speed"}` in, raw 24 kHz mono float32 PCM out until the server closes the connection. A client disconnecting stops synthesis. Normalizes dots that espeak-ng would read as sentence ends (`5.5.`, `1.2.3`, `example.com`, `e.g.`). |
| `server/Dockerfile` | Image for the GPU backend: `python:3.12-slim`, kokoro-onnx, onnxruntime-gpu with the CUDA runtime as pip wheels. The model is mounted at run time, not baked in. |
| `server/say.py` | Standalone script to compare voices (`say.py VOICE "text" [--speed] [--out f.wav] [--list]`). |
| `bin/kokoro-speak` | Client for keys and scripts. Sends the whole text in one request into one `pw-play`, so there's no start-up gap between sentences. Only one instance speaks at a time. |
| `speechd/sd_kokoro` | Speech Dispatcher output module (Python, standard library only). Pause, resume and stop cut off immediately, and index marks are reported as sentences finish. |
| `omarchy/install.sh` | Omarchy installer (details below). |
| `omarchy/kokoro-tts-docker.service`, `omarchy/kokoro-tts-native.service` | The two versions of the `kokoro-tts` user unit. The installer installs one. |
| `omarchy/bindings.lua` | Template for the Hyprland keys block. |
| `fedora/` | The original Fedora Silverblue setup: distrobox unit, `speechd.conf`, `sd_generic` config, Chrome launcher, Orca settings, libspiel RPM spec, backups. |

Paths and environment used by all parts:

| | Default | Override |
|---|---|---|
| Socket | `$XDG_RUNTIME_DIR/kokoro-tts/kokoro-tts.sock` | `KOKORO_SOCKET` |
| Model files | next to `server.py` | `KOKORO_MODEL_DIR` |
| Log the spoken text | off | `KOKORO_LOG_TEXT=1` (in `~/.config/kokoro-tts/env` on Omarchy) |

## Omarchy

```bash
git clone https://github.com/girlpocalypse/sloptts && cd sloptts
./omarchy/install.sh                 # server, kokoro-speak, keys
./omarchy/install.sh --with-speechd  # additionally: Speech Dispatcher + Chrome Read aloud
./omarchy/install.sh --uninstall
```

Then select some text and press **Super + Alt + S**. Press it again to stop.
**Super + Alt + Shift + S** reads the clipboard.

### What the installer does

1. **Checks the keys first.** If Super+Alt+S or Super+Alt+Shift+S is already bound
   (`hyprctl binds`), it stops and names the binding. Change `KEY_*` at the top of the
   script to pick others, or pass `--no-bindings`.
2. **Downloads the model** (`kokoro-v1.0.onnx` and `voices-v1.0.bin`, 350 MB) to
   `~/.local/share/kokoro-tts/`, unless the files are already there.
3. **Picks a backend.** Rootless Docker is used **only if a container can use the
   GPU**. Otherwise the server runs natively on the CPU.
   - **Docker (GPU):** needs rootless Docker (`docker info` lists `rootless`) and the
     NVIDIA container toolkit. It tries `--device nvidia.com/gpu=all` (CDI), then
     `--gpus all`, and builds the image (a few GB of CUDA wheels). Before using the image
     it checks that the model actually loads on CUDA; if it doesn't, it removes the
     image and falls back to native. The container runs with `--network none` and a
     read-only root. Only `$XDG_RUNTIME_DIR/kokoro-tts/` is shared with it (not the
     whole runtime directory, which holds the Wayland and PipeWire sockets). The
     container's root is you under rootless Docker, so the 0600 socket belongs to you.
   - **Native (CPU):** a Python 3.12 venv made by `uv` in
     `~/.local/share/kokoro-tts/.venv`. On the i7-8700 this ran at a real-time factor of
     about 0.27, which is fine for listening.
   - If the GPU isn't reachable, the installer prints NVIDIA's rootless setup steps. Force
     a backend with `--backend docker|native`.
4. **Installs** `kokoro-speak` to `~/.local/bin` and the `kokoro-tts` user service
   (enabled and started).
5. **Adds the keys** as a marked block (`-- >>> sloptts` … `-- <<< sloptts`) at the end of
   `~/.config/hypr/bindings.lua` (after a timestamped backup), then runs
   `hyprctl reload` and `hyprctl configerrors`.
6. **With `--with-speechd`:**
   - Installs `speech-dispatcher` and `sd_kokoro`.
   - Writes `~/.config/speech-dispatcher/speechd.conf` with kokoro as the only module,
     keeping the original as `speechd.conf.sloptts-orig`.
   - Adds `--enable-speech-dispatcher` to `~/.config/chrome-flags.conf`, which the AUR
     `google-chrome` launcher reads. Quit Chrome fully (**⋮ → Exit**) afterwards.

Packages come from `omarchy pkg add`: `jq`, `wl-clipboard`, `libnotify`, `uv` (native
only) and `speech-dispatcher` (optional). The installer doesn't install Docker or the
NVIDIA toolkit. Re-running it is safe; it replaces its own key block and unit.

### Checking it

```bash
systemctl --user status kokoro-tts
journalctl --user -u kokoro-tts -f   # "providers: CUDAExecutionProvider" on the GPU backend
kokoro-speak "Hello from Kokoro."
kokoro-speak --voice bf_emma --speed 1.2 "Different voice"
spd-say "hello"                      # with --with-speechd
```

If the key does nothing, a notification tells you why (no text selected, or the server
isn't running).

## How it fits together

```
Hyprland key ─▶ kokoro-speak ───────────────────────┐
                                                    ├─ Unix socket ─▶ server.py
Chrome / spd-say ─▶ speech-dispatcher ─▶ sd_kokoro ─┘
                                                                      (Docker + CUDA, or native venv)
                                                                      │
      each client plays the PCM it gets back with pw-play ◀───────────┘
```

`sd_kokoro` splits each message at Speech Dispatcher's sentence marks
(`<mark name="__spd_N"/>`). One thread fetches each sentence's audio ahead of playback,
and another writes it to a single `pw-play` and reports each mark once the sentence
before it has played. Pause or stop kills `pw-play` and closes the socket, and the
server abandons the rest of the text. `kokoro-speak` needs no marks, so it sends
everything at once.

## Fedora Silverblue (the original setup)

Collected on 2026-09-24 on Fedora Silverblue 44 (GNOME, Wayland, Orca 50.2, Speech
Dispatcher 0.12.1, Chrome 154). There, Kokoro was the system voice through Speech
Dispatcher for Orca, `spd-say` and Chrome's Reading mode, with working pause, resume
and stop. The Fedora-specific files live in `fedora/`. The shared files were moved:
`kokoro-server/` became `server/`, and `speech-dispatcher/sd_kokoro` became
`speechd/sd_kokoro`.

The socket moved from `$XDG_RUNTIME_DIR/kokoro-tts.sock` to
`$XDG_RUNTIME_DIR/kokoro-tts/kokoro-tts.sock`. When you update a Fedora install from here,
update both `server.py` and `sd_kokoro` together (or set `KOKORO_SOCKET`).

| Here | Installed at | What it is |
|---|---|---|
| `server/server.py`, `server/say.py` | `~/kokoro/` | Server and voice comparison script, run inside the `kokoro` distrobox. |
| `fedora/systemd/kokoro-tts.service` | `~/.config/systemd/user/` | Starts the server at login through `distrobox enter -T kokoro`. |
| `speechd/sd_kokoro` | `~/.local/libexec/speech-dispatcher-modules/sd_kokoro` | **Current** Speech Dispatcher module (uses host `/usr/bin/python3`). |
| `fedora/speech-dispatcher/speechd.conf` (+ `.diff` against `/etc`) | `~/.config/speech-dispatcher/speechd.conf` | Loads only `kokoro` (`sd_kokoro`), `DefaultModule kokoro`. espeak-ng commented out. |
| `fedora/speech-dispatcher/modules/kokoro-generic.conf` | `~/.config/speech-dispatcher/modules/kokoro.conf` | **Old** `sd_generic` config, used only if you switch back to the commented `sd_generic` line. |
| `fedora/bin/kokoro-say` | `~/.local/bin/kokoro-say` | Host client for the old `sd_generic` path (rate maps to speed as `2**(rate/100)`). |
| `fedora/chrome/google-chrome.desktop` (+ `.diff`) | `~/.local/share/applications/google-chrome.desktop` | Adds `--enable-speech-dispatcher` to every `Exec=` line. |
| `fedora/orca/orca-settings-current.ini` | dconf `/org/gnome/orca/` | Orca set to `speechdispatcherfactory`, synthesizer `kokoro`. `orca-settings-before.ini` is the state before. |
| `fedora/spiel/libspiel.spec` | `~/rpmbuild/SPECS/` | RPM spec for libspiel 1.0.4 with libspeechprovider 1.0.3 bundled, layered with `rpm-ostree`. |
| `fedora/spiel/original-plan.md` | `~/.claude/plans/` | The original plan for switching Orca to Spiel. |
| `fedora/backups/` | `~/kokoro/*.bak` | Copies taken before each change. |

### Rebuilding on Fedora

1. **Model and server environment** (in a distrobox, so nothing is layered):
   ```bash
   distrobox create -Y -n kokoro -i registry.fedoraproject.org/fedora-toolbox:44
   distrobox enter kokoro -- sudo dnf install -y uv pipewire-utils python3.12
   mkdir -p ~/kokoro && cd ~/kokoro
   curl -LO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
   curl -LO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
   distrobox enter kokoro -- sh -c 'cd ~/kokoro && uv venv --python /usr/bin/python3.12 .venv && uv pip install --python .venv kokoro-onnx soundfile'
   cp server/*.py ~/kokoro/
   ```
2. **Service:** copy `fedora/systemd/kokoro-tts.service` to `~/.config/systemd/user/`, then
   run `systemctl --user daemon-reload && systemctl --user enable --now kokoro-tts`.
3. **Speech Dispatcher:** copy `speechd/sd_kokoro` to
   `~/.local/libexec/speech-dispatcher-modules/` (make it executable) and
   `fedora/speech-dispatcher/speechd.conf` to `~/.config/speech-dispatcher/`. Then run
   `killall speech-dispatcher`.
4. **Orca:** `dconf load /org/gnome/orca/ < fedora/orca/orca-settings-current.ini`
5. **Chrome:** add `--enable-speech-dispatcher` to the `Exec=` lines of a user copy of
   `google-chrome.desktop`, then fully restart Chrome (**⋮ → Exit**).
6. Check with `spd-say "hello"`, `spd-say -O`, `spd-say -L -o kokoro`, and
   `journalctl --user -u kokoro-tts -f`.

`bin/kokoro-speak` works on Fedora too. Copy it to `~/.local/bin` and bind it to a GNOME
custom shortcut. Without `wl-paste`, it can only speak text given as an argument or on
stdin.

## Lessons learned

- **Chrome on Linux ignores system TTS without `--enable-speech-dispatcher`.** Without it,
  Reading mode's "system" voice is Chrome's built-in `WasmTtsEngine`.
- **Chrome picks its own voice every time.** Speech Dispatcher's `DefaultModule` is
  ignored. Chrome enumerates every module's voices and scores them by language; ties go to
  the first listed. So: load only the kokoro module, list Heart first, and give each voice
  name exactly **one** language. Chrome keys voices by name + module, so Heart listed
  under en/en-US/en-GB showed up as a British voice.
- **A self-playing module must decline server audio.** Speech Dispatcher first offers
  `audio_output_method=server`. Accepting it while playing audio yourself makes the server
  turn `704 PAUSE` into END for clients. `sd_kokoro` replies `303` to that offer.
- **`sd_generic` can't pause mid-utterance.** It only stops at the end of a chunk. If a
  pause lands on the last chunk, the utterance ends, Chrome gets END, and its
  `Resume()` bails out (`if (!paused_ || !is_speaking_) return;`), so Reading mode hangs.
  Hence `sd_kokoro`.
- **`sd_generic` gotchas:** `$RATE` is `rate * GenericRateMultiply / 100` rounded to an
  integer (use `GenericRateMultiply 100` to get -100..100). For a repeated language+voice
  type, the **last** `AddVoice` line wins.
- **Speech Dispatcher 0.12 user module dir** is `~/.local/libexec/speech-dispatcher-modules`
  (computed as `$XDG_DATA_HOME/../libexec/...`), which works on read-only `/usr`.
- **Dots inside tokens can become sentence ends.** kokoro-onnx phonemizes with
  espeak-ng while keeping punctuation. A decimal followed by more punctuation
  (`"Opus 5.5."`) came out as `fˈaɪv. fˈaɪv` ("five. five"), and `example.com` gets a
  `.` inside it that kokoro-onnx pauses on. `server.py` spells these out before
  phonemizing. (kokoro-onnx 0.6.1; `"Opus 5.5"` with nothing after it was already
  fine there.)
- **Spiel doesn't reach Chrome** (Fedora). Orca 50 supports Spiel (it needs libspiel's
  `Spiel-1.0.typelib`, which Fedora doesn't package, hence `fedora/spiel/`). Chrome and most
  apps use Speech Dispatcher instead. Piper through Spiel (`en_GB-cori-high`) was a step on
  the way; Kokoro sounded clearly better.
- **Orca's saved settings can override the default** (Fedora). A named synthesizer (e.g.
  `espeak-ng`) makes Orca pin that module. Mixed settings (`speech-server-factory='spiel'`
  plus `synthesizer='espeak-ng'`) caused random voice switching.
- **Host `python3` was Homebrew's** on the Fedora box. Use `/usr/bin/python3` there for
  GObject bindings (`gi`) and the `speechd` module.
- **onnxruntime-gpu and onnxruntime can't both be installed.** They install the same
  `onnxruntime` module, and kokoro-onnx depends on the CPU one, so the Dockerfile removes
  it and reinstalls the GPU build over it. Without the CUDA libraries, the server logs
  the failure and runs on the CPU rather than crashing.

## Not done / ideas

- The Docker GPU path was tested only with stubs (no GPU or Docker daemon in the build
  environment). The pip recipe and the CPU fallback were tested for real.
- **Edge TTS** (cloud Microsoft neural voices through the unofficial `edge-tts`) was
  considered but not tried.
- A Spiel provider for Kokoro could be written with libspeechprovider.
- Pause/resume for `kokoro-speak`: stopping `pw-play` with SIGSTOP isn't reliable with
  PipeWire, so it only has stop.
