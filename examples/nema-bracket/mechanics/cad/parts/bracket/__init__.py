"""Node `bracket` of nema-bracket: a 2020 profile piece (the client's own, for context), the printed bracket on its top slot,
the NEMA 17 on the bracket's plate, 4x M3 into the motor, 2x M5 into T-nuts in the profile slot."""
import params as P_
from build123d import Pos, Rot
from geom import place

from parts.fasteners import screw
from . import bracket, motor, profile, tnut


def build():
    L = P_.PROFILE_LEN_MM
    prof = profile.build()
    br, mo = bracket.build(), motor.build()
    out = [place(prof, Pos(-L / 2, 0, -P_.PROFILE_SIZE_MM / 2) * Rot(0, 90, 0))]   # lies along X, slot face x- up
    prof["shape"].joints[f"x-_{L / 2:g}"].connect_to(br["shape"].joints["base"])
    out.append(place(br))
    br["shape"].joints["motor"].connect_to(mo["shape"].joints["flange"])
    out.append(place(mo))
    for i in range(1, 5):
        s = screw.build("M3", 8, "DIN 912")
        br["shape"].joints[f"m3_{i}"].connect_to(s["shape"].joints["head"])
        out.append(place(s))
    for i in (1, 2):
        s, n = screw.build("M5", 10, "DIN 912"), tnut.build()
        br["shape"].joints[f"m5_{i}"].connect_to(s["shape"].joints["head"])
        br["shape"].joints[f"tnut_{i}"].connect_to(n["shape"].joints["seat"])
        out += [place(s), place(n)]
    return out
