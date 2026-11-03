#!/bin/sh
# model -> standard viewer at public/ root. kit.json `site`: the router runs it on the worker after every turn that changed the model; locally `de build`
# derive.py brings the model up to date first (cad_incremental --model assemble.py: a no-op when no node changed);
# the viewer files come from the per-part meshes derive.py caches, so a one-part change re-meshes one part.
set -e
S=${DE_ROOT:-/opt/design-engineer}/skills   # DE_ROOT = the plugin root (`de` sets it); unset on the platform
python -u $S/machine-project/scripts/derive.py viewer build/kit/viewer.json
rm -rf build/import && cp -r "$(python3 -c "import json;print(json.load(open('build/kit/viewer.json'))['viewer_dir'])")" build/import
env -u VIRTUAL_ENV uv run --quiet --isolated --no-project --with numpy python $S/step-import/scripts/bake_edges.py build/import
rm -rf build/site
python3 $S/web-viewer/scripts/cycle_from_params.py --params mechanics/cad/params.py --out build/motion.json
python3 $S/web-viewer/templates/assembly/build.py --in build/import --out build/site --up z --title "Wired box" --motion build/motion.json
mkdir -p public && find public -mindepth 1 -maxdepth 1 ! -name status.json ! -name model.json ! -name review -exec rm -rf {} +
cp -r build/site/. public/
python3 $S/machine-project/scripts/aas_sync.py .
