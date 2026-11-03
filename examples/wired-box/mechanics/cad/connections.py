"""The wiring of wired-box: the electric scheme (WIRES) and the wires' routes in 3D (WAYPOINTS, "via").
Every route point comes from params, so a moved part moves its wires."""
import params as P_

z, c = P_.SLOT_Z_MM, P_.CLEAR_MM
WAYPOINTS = {
    "hdr": (P_.HEADER_X_MM + 20, P_.SLOT_Y_MM, z),         # the bundle forms past the header
    "slot": (0.0, P_.SLOT_Y_MM, z),                         # through the divider's slot
    # past the slot the switch wires leave the bundle (a tie on the divider's face), up and over towards the carriage
    "fix": (c, P_.SLOT_Y_MM + P_.SLOT_W_MM / 2 + 2, z + P_.SLOT_H_MM / 2),
    # the clip on the carriage, behind and above it: it moves with the carriage
    "car": {"at": (-P_.CARRIAGE_L_MM / 2 - 5, 0.0, P_.CARRIAGE_H_MM + P_.SWITCH_H_MM + 2 * c + 1), "on": "carriage"},
    # the power pair turns from the jack up towards the header, inside the -X wall
    "pwr": (-P_.INNER_L_MM / 2 + P_.JACK_LEN_MM + 18, P_.JACK_Y_MM, P_.JACK_Z_MM + 2 * c),
}
MOTOR = ["hdr", "slot"]
WIRES = [{"from": f"board.J1.{i + 1}", "to": f"motor.{p}", "net": f"motor {p}", "spec": "0.25 mm²",
          "via": MOTOR} for i, p in enumerate(["A+", "A-", "B+", "B-"])]
WIRES += [
    {"from": "board.J1.5", "to": "switch.NO", "net": "LIMIT", "spec": "0.14 mm²", "via": MOTOR + ["fix", "car"], "loop": 30},
    {"from": "board.J1.6", "to": "switch.C", "net": "0 V", "spec": "0.14 mm²", "via": MOTOR + ["fix", "car"], "loop": 30},
    {"from": "jack.+", "to": "board.J1.7", "net": "+12 V", "spec": "0.5 mm²", "via": ["pwr"]},
    {"from": "jack.-", "to": "board.J1.8", "net": "0 V", "spec": "0.5 mm²", "via": ["pwr"]},
]
