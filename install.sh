#!/bin/sh
# The design engineer (design-engineer plugin) for Claude Code, in one command:
#   curl -fsSL https://raw.githubusercontent.com/nkvch/design-engineer/main/install.sh | sh
#   sh install.sh [--no-claude] [--extras sim,fea,render,electronics]   (from a checkout: installs its current commit)
# Works from the plugin folder of this repo (agent/) and from the root of a repo that is only the plugin.
# What it does: uv (if missing), the Python environment of the core (build123d & co, ~1 GB, once), `de` in ~/.local/bin, and the
# Claude Code plugin (marketplace + install). --no-claude skips the plugin. Nothing needs a key; Telegram / GitHub / the router
# are separate modules, off.
set -eu
REPO=${DE_REPO:-https://github.com/nkvch/design-engineer.git}
CLAUDE=1
EXTRAS=
CC=${CLAUDE_BIN:-claude}   # the Claude Code CLI
while [ $# -gt 0 ]; do
  a=$1; shift
  case $a in
    --no-claude) CLAUDE=0 ;;
    --extras) EXTRAS=${1:-}; shift ;;
    --extras=*) EXTRAS=${a#--extras=} ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
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
for x in $(printf '%s' "$EXTRAS" | tr ',' ' '); do set -- "$@" --extra "$x"; done
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

say ""
say "Done. A machine project in any folder:"
say "  mkdir my-machine && cd my-machine && de init && claude"
say "then ask, e.g. \"design a NEMA 17 motor bracket for a 2020 profile\". The 3D viewer: de serve"
