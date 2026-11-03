"""Part `devkit`: ESP32-DevKitC (ESP32-WROOM-32). Its ports are the GPIOs on its pin headers; `sim` names the firmware build."""
from build123d import Align, Box, Pos
from geom import P

GPIOS = (0, 2, 4, 5, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33, 34, 35, 36, 39)
PORTS = {**{f"GPIO{n}": "" for n in GPIOS}, "GND": "", "3V3": "", "5V": ""}


def build():
    shape = Box(28, 55, 1.6, align=(Align.CENTER, Align.CENTER, Align.MIN)) + Pos(0, 12, 1.6) * Box(18, 25.5, 3.1, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return P("ESP32-DevKitC", shape, "#2b8a3e", process="bought", kind="electrical", ports=PORTS, sim={"model": "esp32", "fw": "player"})
