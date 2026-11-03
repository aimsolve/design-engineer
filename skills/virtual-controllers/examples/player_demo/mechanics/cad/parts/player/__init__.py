"""Node `player`: the case lies at the origin; the display, buttons, encoder and the ESP32 board stand where params put them."""
from build123d import Pos
import params as P_
from geom import place
from . import case, display, button, encoder, devkit


def build():
    z = P_.CASE_FRONT_Z_MM
    out = [place(case.build(), Pos(0, 0, 0)),
           place(devkit.build(), Pos(0, -4, P_.WALL_MM + 1), name="mcu"),
           place(display.build(), Pos(0, P_.SCREEN_Y_MM, z - P_.LCD_THICK_MM), name="lcd")]
    for i, n in enumerate(("btn_prev", "btn_play", "btn_next")):
        out.append(place(button.build(), Pos((i - 1) * P_.BUTTON_PITCH_MM, P_.BUTTON_Y_MM, z - 6), name=n))
    out.append(place(encoder.build(), Pos(0, P_.ENCODER_Y_MM, z - 9), name="enc"))
    return out
