#!/usr/bin/env bash
# Install the Kokoro TTS setup on Omarchy: the server as a user service, the
# kokoro-speak client, and Hyprland keys to speak the selection or clipboard.
# Safe to re-run. Everything is per-user; only package installs need root.
#
#   ./omarchy/install.sh                  install (backend picked automatically)
#   ./omarchy/install.sh --with-speechd   also: Speech Dispatcher module + Chrome flag
#   ./omarchy/install.sh --no-bindings    skip the Hyprland keys
#   ./omarchy/install.sh --uninstall      remove it again (keeps the model files)
#
# Backend: rootless Docker on the GPU when a container can use the GPU, otherwise
# a native Python venv on the CPU. Force one with --backend docker|native.
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DATA=$HOME/.local/share/kokoro-tts
ENV_DIR=$HOME/.config/kokoro-tts
UNIT_DIR=$HOME/.config/systemd/user
BIN=$HOME/.local/bin
MODULE_DIR=$HOME/.local/libexec/speech-dispatcher-modules
SPEECHD_DIR=$HOME/.config/speech-dispatcher
CHROME_FLAGS=$HOME/.config/chrome-flags.conf
BINDINGS=$HOME/.config/hypr/bindings.lua

IMAGE=sloptts-kokoro
BASE_IMAGE=python:3.12-slim
MODEL_URL=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
MODEL_FILES=(kokoro-v1.0.onnx voices-v1.0.bin)

# Hyprland keys: modmask is SUPER=64 + ALT=8 (+ SHIFT=1), used to look for clashes.
# Not Super+Alt+S: stock Omarchy binds it to "Move window to scratchpad".
KEY_SELECTION="SUPER + ALT + R"
KEY_CLIPBOARD="SUPER + ALT + SHIFT + R"
KEY_CHECKS=("72 R" "73 R")

BACKEND=auto
WITH_SPEECHD=0
BINDINGS_WANTED=1
ACTION=install

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; }

while (($#)); do
  case $1 in
    --backend) BACKEND=${2:?--backend needs docker, native or auto}; shift ;;
    --backend=*) BACKEND=${1#*=} ;;
    --with-speechd) WITH_SPEECHD=1 ;;
    --no-bindings) BINDINGS_WANTED=0 ;;
    --uninstall) ACTION=uninstall ;;
    -h | --help) usage; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
  shift
done
[[ $BACKEND =~ ^(auto|docker|native)$ ]] || die "--backend must be auto, docker or native"

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

# --- backend ------------------------------------------------------------------

docker_rootless() {
  have docker && docker info --format '{{.SecurityOptions}}' 2>/dev/null | grep -q rootless
}

# Print the docker run arguments that give a container the GPU, or fail
docker_gpu_args() {
  local args
  for args in "--device nvidia.com/gpu=all" "--gpus all"; do
    # shellcheck disable=SC2086  # $args is meant to split
    if docker run --rm $args "$BASE_IMAGE" nvidia-smi -L >/dev/null 2>&1; then
      echo "$args"
      return 0
    fi
  done
  return 1
}

gpu_help() {
  cat >&2 <<'EOF'
  Rootless Docker can't use the GPU yet. NVIDIA's setup for rootless mode:
    omarchy pkg add nvidia-container-toolkit
    nvidia-ctk runtime configure --runtime=docker --config="$HOME/.config/docker/daemon.json"
    sudo nvidia-ctk config --set nvidia-container-cli.no-cgroups --in-place
    sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
    systemctl --user restart docker
  Check with: docker run --rm --device nvidia.com/gpu=all python:3.12-slim nvidia-smi -L
  Then re-run this installer.
EOF
}

# Can the built image load the model on CUDA? (the real test, not just nvidia-smi)
docker_cuda_works() {
  # shellcheck disable=SC2086
  docker run --rm $1 --network none -v "$DATA:/models:ro" "$IMAGE" python -c '
import sys, onnxruntime as o
o.preload_dlls()
s = o.InferenceSession("/models/kokoro-v1.0.onnx", providers=["CUDAExecutionProvider"])
sys.exit("CUDAExecutionProvider" not in s.get_providers())'
}

download_models() {
  mkdir -p "$DATA"
  local file
  for file in "${MODEL_FILES[@]}"; do
    if [[ ! -s $DATA/$file ]]; then
      say "Downloading $file"
      curl -fL --progress-bar -o "$DATA/$file.part" "$MODEL_URL/$file"
      mv "$DATA/$file.part" "$DATA/$file"
    fi
  done
}

setup_docker() {  # $1: GPU args
  say "Building the $IMAGE image (onnxruntime-gpu with CUDA wheels, a few GB)"
  docker build -t "$IMAGE" "$REPO/server"
  say "Checking that the model loads on CUDA"
  if ! docker_cuda_works "$1"; then
    docker rmi "$IMAGE" >/dev/null 2>&1 || true
    return 1
  fi
  mkdir -p "$ENV_DIR"
  local log_text=""
  [[ -f $ENV_DIR/env ]] && log_text=$(grep '^KOKORO_LOG_TEXT=' "$ENV_DIR/env" || true)
  printf 'KOKORO_GPU_ARGS=%s\n%s\n' "$1" "$log_text" >"$ENV_DIR/env"
  install -Dm644 "$REPO/omarchy/kokoro-tts-docker.service" "$UNIT_DIR/kokoro-tts.service"
}

setup_native() {
  need_packages uv
  say "Creating the Python 3.12 venv in $DATA/.venv"
  [[ -x $DATA/.venv/bin/python ]] || uv venv --python 3.12 "$DATA/.venv"
  uv pip install --python "$DATA/.venv/bin/python" kokoro-onnx
  install -m644 "$REPO/server/server.py" "$DATA/server.py"
  install -Dm644 "$REPO/omarchy/kokoro-tts-native.service" "$UNIT_DIR/kokoro-tts.service"
}

install_server() {
  download_models
  local gpu_args=""
  if [[ $BACKEND != native ]]; then
    if ! docker_rootless; then
      [[ $BACKEND == docker ]] && die "no rootless Docker found (docker info doesn't list rootless)"
      say "No rootless Docker: using the native CPU backend"
    elif ! gpu_args=$(docker_gpu_args); then
      gpu_help
      [[ $BACKEND == docker ]] && die "the Docker backend is only used with the GPU"
      say "Using the native CPU backend for now"
    fi
  fi
  if [[ -n $gpu_args ]]; then
    say "Rootless Docker can use the GPU ($gpu_args)"
    if ! setup_docker "$gpu_args"; then
      [[ $BACKEND == docker ]] && die "the model didn't load on CUDA in the container"
      warn "the model didn't load on CUDA in the container; using the native CPU backend"
      setup_native
    fi
  else
    setup_native
  fi
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
  if have docker && docker image inspect "$IMAGE" >/dev/null 2>&1; then
    docker rmi "$IMAGE" >/dev/null
  fi

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
  say "Done. Model files and the venv are still in $DATA (about 350 MB); remove them by hand if you like."
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
