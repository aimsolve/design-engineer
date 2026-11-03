"""wired-box: the numbers. Single source of truth for mechanics/cad; assemble.py, connections.py and aas_sync.py read this file.

A small printed enclosure split by a divider: a driver board and a power jack on the left, a stepper motor and a carriage
with a limit switch on the right; the wires run through a slot in the divider. The example of wires in 3D (#113): ports
with locations, routes through waypoints, bundles, cut lengths, the harness check.
Units: mm, s. Axes: X to the viewer's right, Y away from him, Z up; the box's floor top at Z = FLOOR_MM, centred on X and Y.
"""

# --- inputs
INNER_L_MM = 180.0              # inside, along X
INNER_W_MM = 90.0               # inside, along Y
INNER_H_MM = 50.0
WALL_MM = 2.0
DIVIDER_H_MM = 40.0             # above the floor
SLOT_W_MM = 12.0                # the divider's wire slot, along Y
SLOT_H_MM = 8.0                 # along Z
SLOT_Y_MM = 0.0
SLOT_Z_MM = 26.0                # its centre, machine Z

BOARD_L_MM, BOARD_W_MM, BOARD_T_MM = 50.0, 36.0, 1.6
STANDOFF_MM = 5.0
BOARD_X_MM, BOARD_Y_MM = -60.0, 0.0
HEADER_H_MM = 8.5               # 2.54 mm pin header above the board
PITCH_MM = 2.54

MOTOR_SQ_MM, MOTOR_LEN_MM = 35.0, 28.0     # NEMA 14 envelope
MOTOR_X_MM, MOTOR_Y_MM = 40.0, -20.0       # back face (connector side) and axis

JACK_Y_MM, JACK_Z_MM = 10.0, 20.0          # DC jack in the -X wall
JACK_D_MM, JACK_LEN_MM, JACK_LUG_MM = 8.0, 14.0, 3.0   # body, its depth inside, lug pitch
CARRIAGE_L_MM, CARRIAGE_W_MM, CARRIAGE_H_MM = 20.0, 14.0, 10.0
SWITCH_L_MM, SWITCH_W_MM, SWITCH_H_MM, SWITCH_LUG_MM = 12.0, 6.0, 7.0, 6.0
CLEAR_MM = 6.0                             # route points this far off a wall or a part

CARRIAGE_X_MM, CARRIAGE_Y_MM = 30.0, 25.0  # carriage at rest (centre)
CARRIAGE_TRAVEL_MM = 25.0                  # along +X
CYCLE_T = 2.0                              # s: out and back

# --- derived
FLOOR_MM = WALL_MM
HEADER_X_MM = BOARD_X_MM + BOARD_L_MM / 2 - 5.0

# the cycle (web-viewer cycle_from_params.py): the carriage slides out and back
GROUPS = {"carriage": ("x", CARRIAGE_TRAVEL_MM)}
MOTION = {"carriage": [(0.0, CYCLE_T / 2, 0.0, 1.0), (CYCLE_T / 2, CYCLE_T, 1.0, 0.0)]}
