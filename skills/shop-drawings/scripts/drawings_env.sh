#!/bin/bash
# Run shop_drawing.py / check_drawings.py in their own venv (build123d 0.13 + ezdxf + matplotlib + shapely + pymupdf), apart from the
# project's own environment.
#   drawings_env.sh shop_drawing.py --parts parts.json --out build/kit
#   drawings_env.sh check_drawings.py build/kit
set -e
V=${SHOP_DRAW_VENV:-/tmp/shopdrawvenv}
[ -x $V/bin/python ] || { uv venv -q $V; uv pip install -q --python $V/bin/python build123d==0.13.0 ezdxf matplotlib pillow shapely pymupdf; }
S="$1"; shift
MPLCONFIGDIR=${MPLCONFIGDIR:-/tmp/mpl} $V/bin/python "$(dirname "$0")/$S" "$@"
