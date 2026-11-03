"""Part `profile` of node `bracket`: the client's 2020 profile, a context piece the bracket sits on (catalogue part,
library.profile.alu_profile), with one mount port in the middle of the slot face that faces up in the node."""
import params as P_
from library.profile import alu_profile


def build():
    L = P_.PROFILE_LEN_MM
    return alu_profile.build(int(P_.PROFILE_SIZE_MM), L, mounts=(("x-", L / 2),))
