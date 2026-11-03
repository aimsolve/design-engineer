"""player_demo: every wire once. The electric scheme and the virtual device (skills/virtual-controllers, device.json) both come from here.
SIM: per-placement settings of the virtual device (keyboard keys, labels) — not part properties, so they live here, not in the part files."""
WIRES = [
    {"from": "mcu.GPIO18", "to": "lcd.SCL", "net": "SPI SCK"},
    {"from": "mcu.GPIO23", "to": "lcd.SDA", "net": "SPI MOSI"},
    {"from": "mcu.GPIO2", "to": "lcd.DC", "net": "LCD DC"},
    {"from": "mcu.GPIO5", "to": "lcd.CS", "net": "LCD CS"},
    {"from": "mcu.GPIO4", "to": "lcd.RST", "net": "LCD RST"},
    {"from": "mcu.GPIO15", "to": "lcd.BLK", "net": "LCD BL"},
    {"from": "mcu.3V3", "to": "lcd.VCC", "net": "+3.3 V"},
    {"from": "mcu.GND", "to": "lcd.GND", "net": "GND"},
    {"from": "mcu.GPIO32", "to": "btn_prev.1", "net": "BTN PREV"},
    {"from": "btn_prev.2", "to": "mcu.GND", "net": "GND"},
    {"from": "mcu.GPIO33", "to": "btn_play.1", "net": "BTN PLAY"},
    {"from": "btn_play.2", "to": "mcu.GND", "net": "GND"},
    {"from": "mcu.GPIO25", "to": "btn_next.1", "net": "BTN NEXT"},
    {"from": "btn_next.2", "to": "mcu.GND", "net": "GND"},
    {"from": "mcu.GPIO26", "to": "enc.A", "net": "ENC A"},
    {"from": "mcu.GPIO27", "to": "enc.B", "net": "ENC B"},
    {"from": "enc.C", "to": "mcu.GND", "net": "GND"},
    {"from": "mcu.GPIO14", "to": "enc.SW", "net": "ENC SW"},
    {"from": "enc.SW2", "to": "mcu.GND", "net": "GND"},
]
HOSES = []
SIM = {
    "btn_prev": {"key": "KeyA", "label": "Back"},
    "btn_play": {"key": "Space", "label": "Play"},
    "btn_next": {"key": "KeyD", "label": "Next"},
    "enc": {"keys": ["ArrowLeft", "ArrowRight"], "push_key": "Enter", "label": "Encoder"},
}
