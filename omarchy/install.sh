#!/usr/bin/env bash
# Install the Kokoro TTS setup on Omarchy: the server as a user service, the
# kokoro-speak client, and Hyprland keys to speak the selection or clipboard.
# The server runs from a uv venv, on an NVIDIA GPU with CUDA when there is one, otherwise
# on the CPU. Safe to re-run. Everything is per-user; only package installs need root.
#
#   ./omarchy/install.sh                  install
#   ./omarchy/install.sh --cpu            use the CPU even if there's an NVIDIA GPU
#   ./omarchy/install.sh --with-speechd   also: Speech Dispatcher module + Chrome flag
#   ./omarchy/install.sh --no-bindings    skip the Hyprland keys
#   ./omarchy/install.sh --uninstall      remove it again (keeps the model files)
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DATA=$HOME/.local/share/kokoro-tts
UNIT_DIR=$HOME/.config/systemd/user
BIN=$HOME/.local/bin
MODULE_DIR=$HOME/.local/libexec/speech-dispatcher-modules
SPEECHD_DIR=$HOME/.config/speech-dispatcher
CHROME_FLAGS=$HOME/.config/chrome-flags.conf
BINDINGS=$HOME/.config/hypr/bindings.lua

# Python is capped by kokoro-onnx (<3.14). Changing a version here upgrades the venv
# on re-run.
PYTHON_VERSION=3.13
KOKORO_ONNX_VERSION=0.6.1
ONNXRUNTIME_VERSION=1.31.0
# model-files-v1.1 re-exports kokoro-v1.0.onnx (same weights) with a duration output,
# which kokoro-onnx 0.6 uses for timestamps. A file that doesn't match is replaced.
MODEL_URL=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1
declare -A MODEL_SHA256=(
  [kokoro-v1.0.onnx]=beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a
  [voices-v1.0.bin]=bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d
)
MODEL_FILES=(kokoro-v1.0.onnx voices-v1.0.bin)

# Hyprland keys: modmask is SUPER=64 + ALT=8 (+ SHIFT=1), used to look for clashes.
# Alt+F9 and Super+Alt+F9 sit next to push-to-talk dictation on F9.
# Not Super+Alt+S: stock Omarchy binds it to "Move window to scratchpad".
KEY_SELECTION="ALT + F9"
KEY_CLIPBOARD="SUPER + ALT + F9"
KEY_CHECKS=("8 F9" "72 F9")

USE_GPU=1
WITH_SPEECHD=0
BINDINGS_WANTED=1
ACTION=install

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

usage() { awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0"; }

while (($#)); do
  case $1 in
    --cpu) USE_GPU=0 ;;
    --with-speechd) WITH_SPEECHD=1 ;;
    --no-bindings) BINDINGS_WANTED=0 ;;
    --uninstall) ACTION=uninstall ;;
    -h | --help) usage; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
  shift
done

# Install the packages that aren't there yet (pacman names)
need_packages() {
  local missing=() pkg
  for pkg in "$@"; do
    pacman -Qq "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
  done
  if ((${#missing[@]})); then
    say "Installing ${missing[*]}"
    omarchy pkg add "${missing[@]}"
  fi
}

backup() { [[ -f $1 ]] && cp -p "$1" "$1.bak.$(date +%s)"; return 0; }

# --- server -------------------------------------------------------------------

sha256_ok() { [[ -s $1 ]] && [[ $(sha256sum "$1" | cut -d' ' -f1) == "$2" ]]; }

download_models() {
  mkdir -p "$DATA"
  local file
  for file in "${MODEL_FILES[@]}"; do
    sha256_ok "$DATA/$file" "${MODEL_SHA256[$file]}" && continue
    if [[ -e $DATA/$file ]]; then say "Replacing $file with the current release"; else say "Downloading $file"; fi
    curl -fL --progress-bar -o "$DATA/$file.part" "$MODEL_URL/$file"
    sha256_ok "$DATA/$file.part" "${MODEL_SHA256[$file]}" || die "$file: checksum mismatch (left in $DATA/$file.part)"
    mv "$DATA/$file.part" "$DATA/$file"
  done
}

has_nvidia_gpu() { have nvidia-smi && nvidia-smi -L >/dev/null 2>&1; }

# Python version and onnxruntime build in the venv, e.g. "3.13 gpu"
venv_state() {
  "$DATA/.venv/bin/python" -I -c '
import sys, importlib.metadata as m
try:
    m.distribution("onnxruntime-gpu")
    build = "gpu"
except m.PackageNotFoundError:
    build = "cpu"
print("%d.%d" % sys.version_info[:2], build)' 2>/dev/null
}

# Does the model load on CUDA in the venv? (the real test, not just nvidia-smi)
cuda_works() {
  ORT_DISABLE_TELEMETRY=1 "$DATA/.venv/bin/python" -I -c '
import sys, onnxruntime as o
o.preload_dlls()
s = o.InferenceSession(sys.argv[1], providers=["CUDAExecutionProvider"])
sys.exit("CUDAExecutionProvider" not in s.get_providers())' "$DATA/kokoro-v1.0.onnx" >/dev/null 2>&1
}

setup_venv() {  # $1: cpu or gpu
  need_packages uv
  local py=$DATA/.venv/bin/python
  # The CPU and GPU builds install the same "onnxruntime" module, so switching between
  # them starts from a fresh venv rather than uninstalling one over the other
  if [[ $(venv_state) != "$PYTHON_VERSION $1" ]]; then
    say "Creating the Python $PYTHON_VERSION venv ($1) in $DATA/.venv"
    uv venv --clear --python "$PYTHON_VERSION" "$DATA/.venv"
  fi
  # --upgrade also moves the unpinned dependencies (numpy, phonemizer) to current releases
  if [[ $1 == gpu ]]; then
    # kokoro-onnx depends on the CPU onnxruntime: leave it out. The [cuda,cudnn] extras
    # bring the CUDA 13 runtime as pip wheels (about 2.5 GB; needs NVIDIA driver 580+).
    uv pip install --upgrade --python "$py" --excludes <(echo onnxruntime) \
      "kokoro-onnx==$KOKORO_ONNX_VERSION" "onnxruntime-gpu[cuda,cudnn]==$ONNXRUNTIME_VERSION"
  else
    uv pip install --upgrade --python "$py" \
      "kokoro-onnx==$KOKORO_ONNX_VERSION" "onnxruntime==$ONNXRUNTIME_VERSION"
  fi
}

install_server() {
  download_models
  if ((USE_GPU)) && has_nvidia_gpu; then
    say "NVIDIA GPU found: installing onnxruntime-gpu with the CUDA libraries"
    setup_venv gpu
    say "Checking that the model loads on CUDA"
    if ! cuda_works; then
      warn "the model didn't load on CUDA (is the NVIDIA driver 580 or newer?); using the CPU"
      setup_venv cpu
    fi
  else
    setup_venv cpu
  fi
  install -m644 "$REPO/server/server.py" "$DATA/server.py"
  install -Dm644 "$REPO/omarchy/kokoro-tts.service" "$UNIT_DIR/kokoro-tts.service"
  systemctl --user daemon-reload
  systemctl --user enable kokoro-tts.service
  systemctl --user restart kokoro-tts.service
}

# --- client and keys ----------------------------------------------------------

install_client() {
  need_packages wl-clipboard libnotify
  install -Dm755 "$REPO/bin/kokoro-speak" "$BIN/kokoro-speak"
}

remove_binding_block() {
  [[ -f $BINDINGS ]] && sed -i '/^-- >>> sloptts/,/^-- <<< sloptts/d' "$BINDINGS"
  return 0
}

reload_hyprland() {
  have hyprctl && hyprctl version >/dev/null 2>&1 || return 0
  hyprctl reload >/dev/null
  local errors
  errors=$(hyprctl configerrors)
  if [[ -n ${errors//[[:space:]]/} && $errors != *"no errors"* ]]; then
    warn "Hyprland reports config errors:"
    printf '%s\n' "$errors" >&2
  fi
}

# Keys already bound to something other than kokoro-speak. Omarchy's Lua binds show
# up as "__lua N" in .arg, so our own keys are also recognised by their description.
key_clashes() {
  have hyprctl && hyprctl version >/dev/null 2>&1 || return 0
  local check mod key
  for check in "${KEY_CHECKS[@]}"; do
    read -r mod key <<<"$check"
    hyprctl binds -j | jq -r --argjson mod "$mod" --arg key "$key" '
      .[] | select(.modmask == $mod and (.key | ascii_upcase) == $key)
          | select((.arg | contains("kokoro-speak")) or (.description | IN("Speak selection", "Speak clipboard")) | not)
          | "\(.description // "") (\(.dispatcher) \(.arg))"'
  done
}

# Run before anything else, so a clash stops the installer before the long parts
check_bindings() {
  [[ -f $BINDINGS ]] || die "$BINDINGS not found; is this Omarchy? (use --no-bindings to skip keys)"
  need_packages jq
  local clashes
  clashes=$(key_clashes)
  if [[ -n $clashes ]]; then
    die "the keys are already in use; change KEY_SELECTION/KEY_CLIPBOARD/KEY_CHECKS in $0:
$clashes"
  fi
}

install_bindings() {
  backup "$BINDINGS"
  remove_binding_block
  sed -e "s|@KEY_SELECTION@|$KEY_SELECTION|" -e "s|@KEY_CLIPBOARD@|$KEY_CLIPBOARD|" -e "s|@BIN@|$BIN|g" \
    "$REPO/omarchy/bindings.lua" >>"$BINDINGS"
  reload_hyprland
}

# --- Speech Dispatcher and Chrome (optional) ----------------------------------

install_speechd() {
  need_packages speech-dispatcher
  install -Dm755 "$REPO/speechd/sd_kokoro" "$MODULE_DIR/sd_kokoro"
  mkdir -p "$SPEECHD_DIR/modules"
  touch "$SPEECHD_DIR/modules/kokoro.conf"  # sd_kokoro reads no config, but speechd wants the file

  local conf=$SPEECHD_DIR/speechd.conf src
  if ! grep -Eq '^AddModule[[:space:]]+"kokoro"[[:space:]]+"sd_kokoro"' "$conf" 2>/dev/null; then
    say "Making kokoro the only Speech Dispatcher module"
    if [[ -f $conf ]]; then
      [[ -f $conf.sloptts-orig ]] || cp -p "$conf" "$conf.sloptts-orig"
      src=$conf
    else
      src=/etc/speech-dispatcher/speechd.conf
      [[ -f $src ]] || die "$src not found; is speech-dispatcher installed?"
    fi
    # Any AddModule line switches off module autodetection, so only kokoro is loaded.
    # Chrome lists every module's voices and ignores DefaultModule, hence one module.
    sed -E -e 's/^([[:space:]]*AddModule[[:space:]])/#\1/' -e 's/^([[:space:]]*DefaultModule[[:space:]])/#\1/' \
      "$src" >"$conf.tmp"
    printf '\n# sloptts\nAddModule "kokoro" "sd_kokoro" "kokoro.conf"\nDefaultModule kokoro\n' >>"$conf.tmp"
    mv "$conf.tmp" "$conf"
  fi
  pkill -x speech-dispatcher || true

  if ! grep -qx -- '--enable-speech-dispatcher' "$CHROME_FLAGS" 2>/dev/null; then
    say "Adding --enable-speech-dispatcher to $CHROME_FLAGS"
    echo '--enable-speech-dispatcher' >>"$CHROME_FLAGS"
    say "Quit Chrome completely (⋮ → Exit) and start it again for Read aloud to use Kokoro"
  fi
}

# --- uninstall ----------------------------------------------------------------

uninstall() {
  say "Stopping and removing the kokoro-tts service"
  systemctl --user disable --now kokoro-tts.service 2>/dev/null || true
  rm -f "$UNIT_DIR/kokoro-tts.service"
  systemctl --user daemon-reload

  rm -f "$BIN/kokoro-speak"
  if [[ -f $BINDINGS ]] && grep -q '^-- >>> sloptts' "$BINDINGS"; then
    backup "$BINDINGS"
    remove_binding_block
    reload_hyprland
  fi

  if [[ -f $MODULE_DIR/sd_kokoro ]]; then
    rm -f "$MODULE_DIR/sd_kokoro" "$SPEECHD_DIR/modules/kokoro.conf"
    local conf=$SPEECHD_DIR/speechd.conf
    if [[ -f $conf.sloptts-orig ]]; then
      mv "$conf.sloptts-orig" "$conf"
    elif grep -q '^# sloptts$' "$conf" 2>/dev/null; then
      rm -f "$conf"
    fi
    pkill -x speech-dispatcher || true
    if [[ -f $CHROME_FLAGS ]]; then
      sed -i '/^--enable-speech-dispatcher$/d' "$CHROME_FLAGS"
    fi
  fi
  say "Done. Model files and the venv are still in $DATA ($(du -sh "$DATA" 2>/dev/null | cut -f1)); remove them by hand if you like."
}

# --- main ---------------------------------------------------------------------

if [[ $ACTION == uninstall ]]; then
  uninstall
  exit 0
fi

have omarchy || warn "the omarchy command wasn't found; this installer is written for Omarchy"
((BINDINGS_WANTED)) && check_bindings
install_server
install_client
((BINDINGS_WANTED)) && install_bindings
((WITH_SPEECHD)) && install_speechd

say "Installed. The server takes a few seconds to load the model; watch it with:"
echo "    journalctl --user -u kokoro-tts -f"
echo "Try: $BIN/kokoro-speak \"Hello from Kokoro.\""
((BINDINGS_WANTED)) && echo "Select text and press $KEY_SELECTION (press again to stop); $KEY_CLIPBOARD reads the clipboard."
exit 0
