#!/bin/sh
# The design engineer (design-engineer plugin) for Claude Code, in one command:
#   curl -fsSL https://raw.githubusercontent.com/aimsolve/design-engineer/main/install.sh | sh
#   sh install.sh [--no-claude] [--no-firmware] [--extras sim,fea,render,electronics,stm32,wasm]   (from a checkout: its current commit)
# Works from the plugin folder of this repo (agent/) and from the root of a repo that is only the plugin.
# What it does: uv (if missing), the Python environment of the core (build123d & co, ~1 GB, once), `de` in ~/.local/bin, the
# firmware tools (PlatformIO, Espressif QEMU for ESP32/S3/C3, simavr for AVR; ~150 MB, toolchains download on the first build),
# and the Claude Code plugin (marketplace + install). --no-claude skips the plugin, --no-firmware the firmware tools.
# Extras stm32 = Renode (STM32 simulator), wasm = emscripten (the viewer's virtual board, ~1.5 GB). No keys, no sudo.
set -eu
REPO=${DE_REPO:-https://github.com/aimsolve/design-engineer.git}
CLAUDE=1
FIRMWARE=1
EXTRAS=
CC=${CLAUDE_BIN:-claude}   # the Claude Code CLI
while [ $# -gt 0 ]; do
  a=$1; shift
  case $a in
    --no-claude) CLAUDE=0 ;;
    --no-firmware) FIRMWARE=0 ;;
    --extras) EXTRAS=${1:-}; shift ;;
    --extras=*) EXTRAS=${a#--extras=} ;;
    -h|--help) sed -n '2,9p' "$0" 2>/dev/null || echo "usage: install.sh [--no-claude] [--no-firmware] [--extras sim,fea,render,electronics,stm32,wasm]"; exit 0 ;;
    *) echo "install.sh: unknown argument $a" >&2; exit 2 ;;
  esac
done
say() { printf '%s\n' "$*"; }
BIN=$HOME/.local/bin
DATA=${XDG_DATA_HOME:-$HOME/.local/share}/design-engineer

# 1. uv
if ! command -v uv >/dev/null 2>&1; then
  [ -x "$BIN/uv" ] || { say "· uv (Python manager) from astral.sh"; curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null; }
  PATH=$BIN:$PATH
fi

# 2. the source: a clean clone in ~/.local/share/design-engineer/src (git-tracked files only: Claude Code copies the plugin folder
#    into its cache as it is, so a checkout's .venv or build output would go with it). Run from a checkout, it clones that.
here=$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)
if [ -n "$here" ] && [ -f "$here/pyproject.toml" ] && [ -d "$here/de" ] && git -C "$here" rev-parse --git-dir >/dev/null 2>&1; then
  REPO=$(git -C "$here" rev-parse --show-toplevel)
fi
SRC=$DATA/src
command -v git >/dev/null 2>&1 || { say "install.sh: needs git" >&2; exit 1; }
say "· source: $REPO"
rm -rf "$SRC.new" && mkdir -p "$DATA" && git clone -q --depth 1 "$( [ -d "$REPO" ] && printf 'file://%s' "$REPO" || printf '%s' "$REPO")" "$SRC.new"
rm -rf "$SRC" && mv "$SRC.new" "$SRC"
PLUG=$SRC/agent   # the plugin folder (it holds the marketplace manifest too)
[ -f "$PLUG/pyproject.toml" ] || PLUG=$SRC

# 3. the core's Python environment (outside the plugin folder: local/bin/de finds it at $DE_VENV)
say "· Python environment (first time: a few minutes)"
mkdir -p "$DATA"
set --
STM32=0 WASM=0
for x in $(printf '%s' "$EXTRAS" | tr ',' ' '); do
  case $x in stm32) STM32=1 ;; wasm) WASM=1 ;; *) set -- "$@" --extra "$x" ;; esac   # stm32, wasm: tools, not Python extras
done
V=${DE_VENV:-$DATA/venv}
UV_PROJECT_ENVIRONMENT=$V uv sync --quiet --frozen --no-dev --inexact --python 3.12 --project "$PLUG" "$@"   # --inexact: extras added earlier stay
rm -f "$V"/.de-lock-* && touch "$V/.de-lock-$(cksum < "$PLUG/uv.lock" | cut -d' ' -f1)"   # what local/bin/de checks before it syncs

# 4. de on PATH
mkdir -p "$BIN"
ln -sf "$PLUG/local/bin/de" "$BIN/de"
"$BIN/de" --help >/dev/null
say "· de -> $BIN/de"
case ":$PATH:" in *":$BIN:"*) ;; *) say "  add to your shell profile: export PATH=\"$BIN:\$PATH\"" ;; esac

# 5. the Claude Code plugin: the marketplace always from this clean clone (one added earlier from a checkout is replaced),
#    then a fresh install, so the plugin cache holds this version's hooks.
cc() {   # a claude call; on failure: what failed, its output, and the commands to run by hand
  out=$("$CC" "$@" 2>&1) && return 0
  say "install.sh: \`claude $*\` failed:" >&2; say "$out" >&2
  say "Run by hand once Claude Code works (logged in): claude plugin marketplace add \"$PLUG\" && claude plugin install design-engineer@design-engineer" >&2
  exit 1
}
if [ "$CLAUDE" = 1 ]; then
  if command -v "$CC" >/dev/null 2>&1; then
    if "$CC" plugin marketplace list 2>/dev/null | grep -q "design-engineer"; then
      cc plugin marketplace remove design-engineer
    fi
    cc plugin marketplace add "$PLUG"
    cc plugin install design-engineer@design-engineer
    say "· Claude Code plugin design-engineer installed"
  else
    say "· Claude Code not found: install it (https://claude.com/claude-code), then run this again"
  fi
fi

# 6. firmware tools in ~/.local/share/design-engineer/tools, each called through a small wrapper in ~/.local/bin (no sudo, no
#    Homebrew). PlatformIO compiles for ESP32 / AVR / RP2 / STM32 (toolchains download on the first build); Espressif QEMU runs
#    ESP32/S3/C3, with the libraries it links taken from conda-forge; simavr (PlatformIO's build) runs AVR. Extras: Renode
#    (stm32), emscripten (wasm: the viewer's virtual board, skills/virtual-controllers). A version already there is not fetched again.
TOOLS=$DATA/tools
PIO_VER=6.2.0
QEMU_TAG=esp-develop-9.2.2-20260417 QEMU_VER=esp_develop_9.2.2_20260417
MAMBA_VER=2.10.0 QEMU_LIBS="pixman=0.46 libgcrypt=1.12 sdl2=2.32"   # conda-forge: the libraries the QEMU builds link
RENODE_VER=1.17.0
EMSDK_VER=3.1.74
OS=$(uname -s) ARCH=$(uname -m)
case $ARCH in arm64|aarch64) ARCH=aarch64 ;; amd64) ARCH=x86_64 ;; esac
MARK='# design-engineer tool wrapper (install.sh)'
WROTE=   # the wrappers this run wrote, for the last message

untar() {   # an archive on stdin (gz, xz, bz2) -> $1, with the core's Python: no xz or bzip2 needed on the machine
  "$V/bin/python" -c 'import sys, tarfile; tarfile.open(fileobj=sys.stdin.buffer, mode="r|*").extractall(sys.argv[1], filter="tar")' "$1"
}
q() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }   # $1 single-quoted for sh
foreign() {   # $BIN/$1 is a file or link this script did not write: it stays, and its tool is not fetched
  { [ -e "$BIN/$1" ] || [ -L "$BIN/$1" ]; } && { [ -L "$BIN/$1" ] || ! grep -q "^$MARK" "$BIN/$1"; } || return 1
  say "  $BIN/$1 is not from this installer: left as it is"
}
wrap() {    # wrap NAME TARGET [VAR=VALUE...]: $BIN/NAME runs TARGET
  name=$1 target=$2; shift 2
  ! foreign "$name" || return 0
  [ -x "$target" ] || { say "install.sh: $name: $target is missing" >&2; exit 1; }
  { echo '#!/bin/sh'; echo "$MARK"
    for e in "$@"; do k=${e%%=*}   # a *PATH variable keeps what the caller had after ours
      case $k in *PATH) echo "export $k=$(q "${e#*=}")\"\${$k:+:\$$k}\"" ;; *) echo "export $k=$(q "${e#*=}")" ;; esac
    done
    echo "exec $(q "$target") \"\$@\""; } > "$BIN/$name"
  chmod 755 "$BIN/$name"
  WROTE="$WROTE $name"
}

fw_platformio() {
  PIO=
  if [ -x "$BIN/pio" ] || command -v pio >/dev/null 2>&1; then
    PIO=$( [ -x "$BIN/pio" ] && echo "$BIN/pio" || command -v pio ); say "· PlatformIO already there, left as it is: $PIO"
  elif [ -e "$BIN/platformio" ] || [ -e "$BIN/piodebuggdb" ]; then   # uv tool install would refuse to replace them
    say "· PlatformIO: $BIN/platformio or piodebuggdb exists, but no pio: skipped (and simavr with it)"
  else
    say "· PlatformIO $PIO_VER"
    UV_TOOL_BIN_DIR=$BIN uv tool install --quiet --python 3.12 "platformio==$PIO_VER"
    PIO=$BIN/pio
  fi
}

fw_simavr() {
  [ -n "$PIO" ] && ! foreign simavr || return 0
  core=${PLATFORMIO_CORE_DIR:-$HOME/.platformio}
  [ -x "$core/packages/tool-simavr/bin/simavr" ] || { say "· simavr (AVR)"; "$PIO" pkg install --global --tool platformio/tool-simavr@1.10800.0 >/dev/null; }
  wrap simavr "$core/packages/tool-simavr/bin/simavr"
}

fw_qemu() {
  case $OS-$ARCH in
    Darwin-aarch64) t=aarch64-apple-darwin m=osx-arm64 ;;  Darwin-x86_64) t=x86_64-apple-darwin m=osx-64 ;;
    Linux-aarch64)  t=aarch64-linux-gnu    m=linux-aarch64 ;; Linux-x86_64) t=x86_64-linux-gnu  m=linux-64 ;;
    *) say "· Espressif QEMU has no build for $OS $ARCH: skipped"; return 0 ;;
  esac
  ! { foreign qemu-system-xtensa && foreign qemu-system-riscv32; } || return 0
  d=$TOOLS/qemu-esp
  v=$QEMU_VER-$MAMBA_VER-$(printf '%s' "$QEMU_LIBS" | tr ' =' '_-')
  if [ ! -f "$d/.v-$v" ]; then
    say "· Espressif QEMU (ESP32, S3, C3)"
    rm -rf "$d" && mkdir -p "$d"
    for a in xtensa riscv32; do
      curl -fsSL "https://github.com/espressif/qemu/releases/download/$QEMU_TAG/qemu-$a-softmmu-$QEMU_VER-$t.tar.xz" | untar "$d"
    done
    libs=$QEMU_LIBS; [ "$OS" = Linux ] && libs="$libs libslirp=4.9"   # Linux links slirp from the system too
    curl -fsSL "https://micro.mamba.pm/api/micromamba/$m/$MAMBA_VER" | untar "$d/mamba"
    # shellcheck disable=SC2086   # $libs: one word per package. HOME too: its dotfiles go with mamba/; no ~/.condarc of the user
    HOME=$d/mamba MAMBA_ROOT_PREFIX=$d/mamba "$d/mamba/bin/micromamba" create --yes --quiet --no-rc --override-channels \
      -p "$d/libs" -c conda-forge $libs >/dev/null
    rm -rf "$d/mamba"
    touch "$d/.v-$v"
  fi
  [ "$OS" = Darwin ] && lp=DYLD_FALLBACK_LIBRARY_PATH || lp=LD_LIBRARY_PATH
  for a in xtensa riscv32; do wrap "qemu-system-$a" "$d/qemu/bin/qemu-system-$a" "$lp=$d/libs/lib"; done
}

fw_renode() {
  case $OS-$ARCH in
    Linux-x86_64) f=linux-portable.tar.gz ;; Linux-aarch64) f=linux-arm64-portable.tar.gz ;; Darwin-aarch64) f=osx-arm64-portable.dmg ;;
    *) say "· Renode $RENODE_VER has no build for $OS $ARCH (Linux, or a Mac with Apple silicon): skipped"; return 0 ;;
  esac
  ! foreign renode || return 0
  d=$TOOLS/renode
  if [ ! -f "$d/.v-$RENODE_VER" ]; then
    say "· Renode $RENODE_VER (STM32)"
    [ ! -d "$d/mnt" ] || hdiutil detach -quiet -force "$d/mnt" 2>/dev/null || true   # left mounted by an interrupted run
    rm -rf "$d" && mkdir -p "$d"
    u=https://github.com/renode/renode/releases/download/v$RENODE_VER/renode-$RENODE_VER.$f
    if [ "$OS" = Darwin ]; then
      curl -fsSL -o "$d/renode.dmg" "$u"
      hdiutil attach -nobrowse -readonly -quiet -mountpoint "$d/mnt" "$d/renode.dmg"
      cp -R "$d/mnt/Renode.app" "$d/" || { hdiutil detach -quiet -force "$d/mnt"; exit 1; }
      hdiutil detach -quiet "$d/mnt"; rm -f "$d/renode.dmg"
    else
      curl -fsSL "$u" | untar "$d"
    fi
    touch "$d/.v-$RENODE_VER"
  fi
  if [ "$OS" = Darwin ]; then wrap renode "$d/Renode.app/Contents/MacOS/renode"
  else wrap renode "$(ls -d "$d"/*/renode | head -1)" DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1; fi   # no libicu needed
}

fw_wasm() {
  ! { foreign emcc && foreign em++; } || return 0
  d=$TOOLS/emsdk
  if [ ! -f "$d/.v-$EMSDK_VER" ]; then
    say "· emscripten $EMSDK_VER (the virtual board; ~1.5 GB, a few minutes)"
    rm -rf "$d" && git clone -q --depth 1 --branch "$EMSDK_VER" https://github.com/emscripten-core/emsdk.git "$d"
    for c in install activate; do   # its download progress bars stay in the log; shown only when a step fails
      EMSDK_PYTHON=$V/bin/python "$d/emsdk" $c "$EMSDK_VER" > "$TOOLS/emsdk.log" 2>&1 || { tail -n 20 "$TOOLS/emsdk.log" >&2; exit 1; }
    done
    touch "$d/.v-$EMSDK_VER"
  fi
  for t in emcc em++; do wrap "$t" "$d/upstream/emscripten/$t" "EMSDK=$d" "EM_CONFIG=$d/.emscripten" "EMSDK_PYTHON=$V/bin/python"; done
  # skills/virtual-controllers/build/build.mjs runs on node: emsdk's, only when the machine has none (a later Node.js install
  # into a directory after ~/.local/bin on PATH would be hidden by it: then delete ~/.local/bin/node)
  if grep -q "^$MARK" "$BIN/node" 2>/dev/null || ! command -v node >/dev/null 2>&1; then wrap node "$(ls -d "$d"/node/*/bin/node | head -1)"; fi
}

[ "$FIRMWARE" = 1 ] || [ "$STM32$WASM" = 00 ] || say "· --extras stm32 / wasm ignored: --no-firmware"
if [ "$FIRMWARE" = 1 ]; then
  trap 'say "install.sh: the firmware tools failed (see above). Run it again, or skip them with --no-firmware" >&2' EXIT
  mkdir -p "$TOOLS"
  fw_platformio
  fw_simavr
  fw_qemu
  [ "$STM32" = 0 ] || fw_renode
  [ "$WASM" = 0 ] || fw_wasm
  trap - EXIT
fi

say ""
say "Done. A machine project in any folder:"
say "  mkdir my-machine && cd my-machine && de init && claude"
say "then ask, e.g. \"design a NEMA 17 motor bracket for a 2020 profile\". The 3D viewer: de serve"
[ -z "$WROTE" ] || say "Firmware tools in $BIN:$WROTE${PIO:+ (PlatformIO: $PIO)}"
