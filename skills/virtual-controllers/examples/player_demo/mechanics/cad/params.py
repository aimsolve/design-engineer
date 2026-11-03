"""player_demo: the numbers. A pocket player (ESP32 + 2.0" ST7789 240x320 + three buttons + an EC11 encoder), the example device of
skills/virtual-controllers. Units: mm. The device lies face up: X to the right, Y away, Z out of the screen."""

# --- inputs
CASE_W_MM = 64.0
CASE_H_MM = 110.0
CASE_D_MM = 16.0
WALL_MM = 2.0

SCREEN_Y_MM = 20.0          # centre of the display module on the face
BUTTON_Y_MM = -16.0
BUTTON_PITCH_MM = 18.0
ENCODER_Y_MM = -38.0

# 2.0" ST7789 module (240 x 320, portrait): board, glass, active area
LCD_PCB_W_MM = 36.0
LCD_PCB_H_MM = 52.0
LCD_GLASS_W_MM = 34.0
LCD_GLASS_H_MM = 44.0
LCD_ACTIVE_W_MM = 30.6
LCD_ACTIVE_H_MM = 40.8
LCD_THICK_MM = 3.0          # board + glass, the glass face is flush with the case face

# --- derived
CASE_FRONT_Z_MM = CASE_D_MM
