#!/bin/sh
# model -> the model page with the virtual device (kit.json `site`). The model build writes build/model/device.json (device.py);
# build/fw comes from sim/build_fw.sh. SKILLS = the framework's skills folder (default: as in the project containers).
set -e
S=${SKILLS:-/opt/design-engineer/skills}
PY=${PYTHON:-python3}
$PY -u "$S/machine-project/scripts/derive.py" viewer build/kit/viewer.json
rm -rf build/import && cp -r "$($PY -c "import json;print(json.load(open('build/kit/viewer.json'))['viewer_dir'])")" build/import
rm -rf build/site
$PY "$S/web-viewer/templates/assembly/build.py" --in build/import --out build/site --up z --title "Player (example)" --sim sim ${STANDALONE:+--standalone}
