"""{machine}: the numbers. Single source of truth for mechanics/cad; assemble.py and aas_sync.py read this file.

Units: mm, kg, N, deg. The unit is the name suffix (_MM, _KG, _N, _DEG, _MM2, _NM, _S ...); no suffix = a count or a ratio.
Axes in words (from the viewer's position): X to his right, Y away from him, Z up.
Only UPPER_CASE int/float/bool constants are numbers of the model (they go to the AAS, TechnicalData.ModelParameters).
A constant a client gave says where it came from in a comment (`# client, wiki D3`); everything else is DERIVED below.
"""

# --- inputs (client / owner decisions)
EXAMPLE_WIDTH_MM = 100.0   # replace with the real inputs, delete this one

# --- derived (never typed twice)
EXAMPLE_HALF_WIDTH_MM = EXAMPLE_WIDTH_MM / 2
