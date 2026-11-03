"""nema-bracket: the numbers. Single source of truth for mechanics/cad; assemble.py and aas_sync.py read this file.

Units: mm, kg, N, deg. The unit is the name suffix (_MM, _KG, _N, _DEG, _MM2, _NM, _S ...); no suffix = a count or a ratio.
Axes in words (from the viewer's position): X to his right, Y away from him, Z up.
The 2020 profile lies along X, its top slot face at Z = 0. The motor shaft points toward the viewer (-Y... see MOTOR_SHAFT_DIR)
Only UPPER_CASE int/float/bool constants are numbers of the model (they go to the AAS, TechnicalData.ModelParameters).
A constant a client gave says where it came from in a comment (`# client, wiki D3`); everything else is DERIVED below.
"""

# --- inputs (client / owner decisions)
PROFILE_SIZE_MM = 20.0          # the user: "2020 profile"
MOTOR_FRAME = 17                # client: «NEMA 17»
PROFILE_LEN_MM = 200.0          # context piece the bracket sits on (the client's own profile)

# NEMA 17 (17HS4401-class), standard flange: NEMA ICS 16
MOTOR_SQ_MM = 42.3              # flange square
MOTOR_LEN_MM = 40.0             # body length, 17HS4401
MOTOR_HOLE_PITCH_MM = 31.0      # M3 holes on a 31 mm square
MOTOR_PILOT_D_MM = 22.0         # centring boss diameter
MOTOR_PILOT_H_MM = 2.0
MOTOR_SHAFT_D_MM = 5.0
MOTOR_SHAFT_LEN_MM = 24.0       # from the flange face

# bracket (printed L with two gussets)
WALL_MM = 5.0                   # base and motor plate thickness
MOTOR_GAP_MM = 1.0              # motor body to base / gussets
GUSSET_T_MM = 4.0
GUSSET_H_MM = 30.0
SLOT_PITCH_MM = 66.0            # M5 slots into the profile's top slot, centre to centre
SLOT_TRAVEL_MM = 8.0            # adjustment along the profile (belt tension)
SLOT_EDGE_MM = 7.0              # slot end to base end
M3_CLEAR_D_MM = 3.4
M5_CLEAR_D_MM = 5.5
PILOT_CLEAR_D_MM = 22.6
TNUT_DEPTH_MM = 1.8             # slot lip of a B-type 2020: T-nut top under the face

# --- derived (never typed twice)
PLATE_W_MM = MOTOR_SQ_MM + 2 * (MOTOR_GAP_MM + GUSSET_T_MM)
BASE_LEN_MM = SLOT_PITCH_MM + SLOT_TRAVEL_MM + 2 * SLOT_EDGE_MM
MOTOR_AXIS_Z_MM = WALL_MM + MOTOR_GAP_MM + MOTOR_SQ_MM / 2
PLATE_H_MM = MOTOR_AXIS_Z_MM + MOTOR_SQ_MM / 2 + 3.0
