"""Part `case`: the printed shell, open at the back; a window for the display glass, holes for the buttons and the encoder shaft."""
import params as P_
from build123d import Align, Box, Cylinder, Pos
from geom import P


def build():
    w, h, d, t = P_.CASE_W_MM, P_.CASE_H_MM, P_.CASE_D_MM, P_.WALL_MM
    shape = Box(w, h, d, align=(Align.CENTER, Align.CENTER, Align.MIN)) - Box(w - 2 * t, h - 2 * t, d - t, align=(Align.CENTER, Align.CENTER, Align.MIN))
    shape -= Pos(0, P_.SCREEN_Y_MM, d - t) * Box(P_.LCD_GLASS_W_MM + 0.6, P_.LCD_GLASS_H_MM + 0.6, t, align=(Align.CENTER, Align.CENTER, Align.MIN))
    for i in (-1, 0, 1):
        shape -= Pos(i * P_.BUTTON_PITCH_MM, P_.BUTTON_Y_MM, d - t) * Cylinder(4.6, t, align=(Align.CENTER, Align.CENTER, Align.MIN))
    shape -= Pos(0, P_.ENCODER_Y_MM, d - t) * Cylinder(3.8, t, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return P("Player case", shape, "#e9ecef", process="print", material="PETG")
