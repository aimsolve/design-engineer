# aas

`wired-box.aas.json` is the machine model (Asset Administration Shell, IEC 63278, metamodel v3): the index of every layer and the home of every number.
Edit it through the layer that owns the number (`aas_sync.py` for build123d params, `aas_build.py` for an imported STEP), then check it:
`python3 <plugin>/skills/machine-project/scripts/check.py .`. Details: `step-import/AAS.md`.
