"""The `bought fastener pairs` rule of fit_checks.py: every thread size in the purchase list has both halves.

Separate from test_fit_static.py because this rule was added after that file's brief was written (it came out of the audit of
the six live purchase lists, where M12 nuts with no M12 bolt and M10 rivet nuts with no M10 screw turned up in two projects).
Static, like the other list rules: a hand-written build/model/parts.json is all it reads.

A thread with only one half is a WARN, never a FAIL: the other half may be a thread cut in a part of the model, and then the
drawing has to say so. What the rule must never do is stay silent, because the manager answers clients out of these rows.
"""
import test_fit_static as S
from samples_i18n import SAMPLES

RU = SAMPLES["ru"]          # the fastener names as a Russian purchase list spells them

PAIRS = "bought fastener pairs"


def screw(pid, text, qty=10):
    return S.row(pid, name=text, kind="fasteners", qty=qty, unit="pcs")


def test_p1_screws_with_no_nut_rivet_nut_or_t_nut_warn_naming_the_size(tmp_path):
    """Two live lists: screws whose mating thread is in no row of the list."""
    root = S.write_model(tmp_path / "p", [
        S.frame_root(),
        screw("screw_m5x12", RU["screw_socket_m5x12"], 14),
        screw("screw_m6x20", RU["bolt_hex_m6x20"], 40),
        screw("nut_m6", RU["nut_hex_m6"], 40),
    ])
    st, _, detail = S.rule_line(root, PAIRS)
    assert st == "WARN", f"a screw size with no female half is a WARN, not {st}: {detail}"
    assert "M5" in detail, f"the warning must name the size that has no pair: {detail}"
    assert "M6" not in detail, f"M6 has both halves and must not be reported: {detail}"


def test_p2_nuts_with_nothing_male_warn_naming_the_size(tmp_path):
    """Two live lists had M12 nuts and no M12 bolt."""
    root = S.write_model(tmp_path / "p", [
        S.frame_root(),
        screw("nut_m12", RU["nut_hex_m12"], 14),
        screw("rivet_m10", RU["rivet_nut_m10"], 12),
    ])
    st, _, detail = S.rule_line(root, PAIRS)
    assert st == "WARN", f"a nut with nothing to screw into it is a WARN, not {st}: {detail}"
    for size in ("M12", "M10"):
        assert size in detail, f"the warning must name {size}: {detail}"


def test_p3_a_t_nut_is_the_female_half_of_its_screw(tmp_path):
    """A machine's tower: M5x10 screws into A264 slot-6 T-nuts — a complete pair, nothing to report."""
    root = S.write_model(tmp_path / "p", [
        S.frame_root(),
        screw("screw_m5x10", RU["screw_socket_m5x10"], 38),
        screw("tnut_m5", RU["tnut_m5"], 36),
    ])
    S.unreported(root, PAIRS, "M5", RU["m_letter"] + "5")


def test_p4_a_rivet_nut_is_the_female_half_of_its_screw(tmp_path):
    """A live list: 32 M6 rivet nuts against 32 M6 screws — the cleanest of the six live lists."""
    root = S.write_model(tmp_path / "p", [
        S.frame_root(),
        screw("screw_m6x10", RU["screw_socket_m6x10"], 32),
        screw("rivet_m6", RU["rivet_nut_m6"], 32),
    ])
    S.unreported(root, PAIRS, "M6", RU["m_letter"] + "6")


def test_p5_a_washer_is_neither_half(tmp_path):
    """A washer carries a thread size but no thread: it must not stand in for the nut its screw needs."""
    root = S.write_model(tmp_path / "p", [
        S.frame_root(),
        screw("screw_m4x8", RU["screw_button_m4x8"], 16),
        screw("washer_m4", RU["washer_m4"], 16),
    ])
    st, _, detail = S.rule_line(root, PAIRS)
    assert st == "WARN", f"a washer does not pair a screw, so M4 is still unpaired: {st}: {detail}"
    assert "M4" in detail, detail
