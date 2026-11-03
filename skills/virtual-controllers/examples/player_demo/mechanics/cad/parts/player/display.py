"""Part `display`: a 2.0" ST7789 240x320 SPI module. `glass` tells the viewer where the picture goes (active area, the part's own mm)."""
import params as P_
from build123d import Align, Box, Pos
from geom import P

PORTS = {"GND": "", "VCC": "3.3 V", "SCL": "SCK", "SDA": "MOSI", "RST": "", "DC": "", "CS": "", "BLK": "backlight"}


def build():
    pcb = Box(P_.LCD_PCB_W_MM, P_.LCD_PCB_H_MM, 1.6, align=(Align.CENTER, Align.CENTER, Align.MIN))
    glass = Pos(0, 0, 1.6) * Box(P_.LCD_GLASS_W_MM, P_.LCD_GLASS_H_MM, P_.LCD_THICK_MM - 1.6, align=(Align.CENTER, Align.CENTER, Align.MIN))
    sim = {"model": "st7789", "w": 240, "h": 320, "ips": True,   # IPS: the firmware switches INVON on for true colours
           "glass": {"center": [0, 0, P_.LCD_THICK_MM], "normal": [0, 0, 1], "up": [0, 1, 0], "size": [P_.LCD_ACTIVE_W_MM, P_.LCD_ACTIVE_H_MM]}}
    return P("Display 2.0\" ST7789 240×320", pcb + glass, "#1d2733", process="bought", kind="electrical", ports=PORTS, sim=sim)
