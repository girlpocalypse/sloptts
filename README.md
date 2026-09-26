# slopvoice
Note from the human who told claude to make this: THIS IS PURE SLOP. I HAVE NOT READ THE CODE. UPLOADED FOR REFERENCES PURPOSES ONLY. I HIGHLY RECOMMEND YOU DO NOT ATTEMPT TO USE THIS AS-IS.

A record of setting up high-quality local text-to-speech on Fedora Silverblue 44
(GNOME, Wayland, Orca 50.2, Speech Dispatcher 0.12.1, Chrome 154). Collected on 2026-09-24.

The result: **Kokoro-82M, voice `af_heart`**, is the system TTS voice through Speech
Dispatcher. It's used by Orca, `spd-say` and Chrome's Reading mode "Read aloud", with
working pause, resume and stop.

These are copies, not the live files. The table below shows where each file is
installed.

## Layout

| Here | Installed at | What it is |
|---|---|---|
| `kokoro-server/server.py` | `~/kokoro/server.py` | Kokoro TTS server. Keeps the model loaded, takes one JSON request per connection on `$XDG_RUNTIME_DIR/kokoro-tts.sock`, and streams raw 24 kHz mono float32 PCM. Runs inside the `kokoro` distrobox. |
| `kokoro-server/say.py` | `~/kokoro/say.py` | Standalone script, used to compare voices (`say.py VOICE "text" [--speed] [--out f.wav] [--list]`). |
| `systemd/kokoro-tts.service` | `~/.config/systemd/user/` | User service that starts the server at login through `distrobox enter -T kokoro`. |
| `speech-dispatcher/sd_kokoro` | `~/.local/libexec/speech-dispatcher-modules/sd_kokoro` | **Current** Speech Dispatcher output module (Python, uses host `/usr/bin/python3`). |
| `speech-dispatcher/speechd.conf` (+ `.diff` against `/etc`) | `~/.config/speech-dispatcher/speechd.conf` | Loads only `kokoro` (`sd_kokoro`), with `DefaultModule kokoro`. espeak-ng is commented out. |
| `speech-dispatcher/modules/kokoro-generic.conf` | `~/.config/speech-dispatcher/modules/kokoro.conf` | **Old** `sd_generic` config. Now only used if you switch back to the commented `sd_generic` line. |
| `bin/kokoro-say` | `~/.local/bin/kokoro-say` | Host client for the old `sd_generic` path (stdin text in, PCM out; rate maps to speed as `2**(rate/100)`). |
| `chrome/google-chrome.desktop` (+ `.diff`) | `~/.local/share/applications/google-chrome.desktop` | Adds `--enable-speech-dispatcher` to every `Exec=` line. |
| `orca/orca-settings-current.ini` | dconf `/org/gnome/orca/` | Orca set to `speechdispatcherfactory`, synthesizer `kokoro`. `orca-settings-before.ini` is the state before. |
| `spiel/libspiel.spec` | `~/rpmbuild/SPECS/` | RPM spec for libspiel 1.0.4 with libspeechprovider 1.0.3 bundled. Built in a distrobox and layered with `rpm-ostree`. |
| `spiel/original-plan.md` | `~/.claude/plans/` | The original plan for switching Orca to Spiel. |
| `backups/` | `~/kokoro/*.bak` | Copies taken before each change (Chrome launcher, `sd_generic` voice table, speechd.conf before `sd_kokoro`). |

Not copied (too large or regenerable): `~/kokoro/kokoro-v1.0.onnx` (325 MB),
`~/kokoro/voices-v1.0.bin` (28 MB), `~/kokoro/.venv`, `~/kokoro/samples/*.wav`, and the
built RPMs in `~/rpmbuild/RPMS/x86_64/`.

## How it fits together

```
Chrome / Orca / spd-say
        │  SSIP
        ▼
speech-dispatcher ── sd_kokoro (host, python3) ── Unix socket ──▶ server.py (kokoro distrobox, kokoro-onnx on CPU)
                         │                                              │
                         └── pw-play --raw (24 kHz mono f32) ◀── PCM ───┘
```

`sd_kokoro` splits each message at Speech Dispatcher's sentence marks (`<mark name="__spd_N"/>`).
One thread fetches each sentence's audio ahead of playback. Another writes it to a single
`pw-play` and reports each mark once the sentence before it has played. Pause or stop
kills `pw-play` and closes the socket, and the server abandons the rest of the text.

## Rebuilding from scratch

1. **Model and server environment** (in a distrobox, so nothing is layered):
   ```bash
   distrobox create -Y -n kokoro -i registry.fedoraproject.org/fedora-toolbox:44
   distrobox enter kokoro -- sudo dnf install -y uv pipewire-utils python3.12
   mkdir -p ~/kokoro && cd ~/kokoro
   curl -LO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
   curl -LO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
   distrobox enter kokoro -- sh -c 'cd ~/kokoro && uv venv --python /usr/bin/python3.12 .venv && uv pip install --python .venv kokoro-onnx soundfile'
   cp kokoro-server/*.py ~/kokoro/
   ```
   Python 3.12 is used because Fedora 44's default Python may be too new for onnxruntime wheels.
2. **Service:** copy `systemd/kokoro-tts.service` to `~/.config/systemd/user/`, then run
   `systemctl --user daemon-reload && systemctl --user enable --now kokoro-tts`.
3. **Speech Dispatcher:** copy `sd_kokoro` to `~/.local/libexec/speech-dispatcher-modules/` (make it executable) and `speechd.conf` to `~/.config/speech-dispatcher/`. Then run `killall speech-dispatcher`.
4. **Orca:** `dconf load /org/gnome/orca/ < orca/orca-settings-current.ini`
5. **Chrome:** add `--enable-speech-dispatcher` to the `Exec=` lines of a user copy of `google-chrome.desktop`, then fully restart Chrome (**⋮ → Exit**).
6. Check with `spd-say "hello"`, `spd-say -O`, `spd-say -L -o kokoro`, and `journalctl --user -u kokoro-tts -f`.

## Lessons learned

- **Spiel doesn't reach Chrome.** Orca 50 supports Spiel (it needs libspiel's
  `Spiel-1.0.typelib`, which Fedora doesn't package, hence `spiel/`). Chrome and most
  apps use Speech Dispatcher instead. Spiel with Piper (`en_GB-cori-high` was the favorite)
  was a step on the way; Kokoro sounded clearly better.
- **Chrome on Linux ignores system TTS without `--enable-speech-dispatcher`.** Without it,
  Reading mode's "system" voice is Chrome's built-in `WasmTtsEngine`.
- **Chrome picks its own voice every time.** Speech Dispatcher's `DefaultModule` is
  ignored. Chrome enumerates every module's voices and scores them by language; ties go to
  the first listed. So: disable espeak-ng, list Heart first, and give each voice name
  exactly **one** language. Chrome keys voices by name + module, so Heart listed under
  en/en-US/en-GB showed up as a British voice.
- **Orca's saved settings can override the default.** A named synthesizer (e.g.
  `espeak-ng`) makes Orca pin that module. Mixed settings (`speech-server-factory='spiel'`
  plus `synthesizer='espeak-ng'`) caused random voice switching.
- **`sd_generic` can't pause mid-utterance.** It only stops at the end of a chunk. If a
  pause lands on the last chunk, the utterance ends, Chrome gets END, and its
  `Resume()` bails out (`if (!paused_ || !is_speaking_) return;`), so Reading mode hangs.
  Hence `sd_kokoro`.
- **A self-playing module must decline server audio.** Speech Dispatcher first offers
  `audio_output_method=server`. Accepting it while playing audio yourself makes the server
  turn `704 PAUSE` into END for clients. `sd_kokoro` replies `303` to that offer.
- **`sd_generic` gotchas:** `$RATE` is `rate * GenericRateMultiply / 100` rounded to an
  integer (use `GenericRateMultiply 100` to get -100..100). For a repeated language+voice
  type, the **last** `AddVoice` line wins.
- **Speech Dispatcher 0.12 user module dir** is `~/.local/libexec/speech-dispatcher-modules`
  (computed as `$XDG_DATA_HOME/../libexec/...`), which works on read-only `/usr`.
- **Host `python3` is Homebrew's.** Use `/usr/bin/python3` for GObject bindings (`gi`) and
  the `speechd` module.

## Not done / ideas

- **GPU:** the RTX 2080 Ti (sm_75, driver 615.71, CUDA 13.4) could run Kokoro through
  `onnxruntime-gpu` 1.30 (CUDA 13 wheels). That needs a distrobox created with `--nvidia`
  (the current one lacks `libcuda.so`). On the i7-8700 CPU it runs at a real-time factor
  of about 0.27, so this was judged not worth it.
- **Edge TTS** (cloud Microsoft neural voices through the unofficial `edge-tts`) was
  considered but not tried.
- A Spiel provider for Kokoro could be written with libspeechprovider.
- Orca "read only what I click" was being investigated when this was collected. Orca
  has mouse review (hover) but no click-only mode.
