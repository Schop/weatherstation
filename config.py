# --- Location ---
LATITUDE = 51.385551
LONGITUDE = 5.714190
LOCATION_NAME = "Someren"

# --- Behaviour ---
REFRESH_INTERVAL = 600  # seconds between API calls
FPS = 10
WIND_WARN_KMH = 30  # wind tile turns red above this speed

# --- Network page ---
# Devices to monitor. Without "port" the host is pinged; with "port" a TCP
# connection is tried instead (use this for hosts that block ping).
NETWORK_DEVICES = [
    # {"name": "NAS",     "host": "192.168.1.20"},
    # {"name": "Printer", "host": "192.168.1.50", "port": 8883},
]

# --- Display ---
SCREEN_WIDTH = 800
SCREEN_HEIGHT = 480

# Layout regions
TOP_BAR_H    = 55
LEFT_PANEL_W = 260
MAIN_H       = 250   # TOP_BAR_H + MAIN_H = FORECAST_Y
FORECAST_Y   = 305   # TOP_BAR_H + MAIN_H
FORECAST_H   = 175   # fills to SCREEN_HEIGHT
HOURLY_CARD_H = 195  # card row height on hourly page

# --- Warm light theme ---
BG_COLOR       = (248, 245, 240)
CARD_COLOR     = (238, 234, 226)
TEXT_PRIMARY   = (38,  35,  32)
TEXT_SECONDARY = (145, 132, 118)
ACCENT_COLOR   = (215, 120, 45)
SUN_COLOR      = (240, 175, 40)
MOON_COLOR     = (140, 140, 180)
RAIN_COLOR     = (75,  140, 215)
SNOW_COLOR     = (140, 190, 230)
STORM_COLOR    = (175, 135, 50)
FOG_COLOR      = (185, 175, 162)
HIGH_COLOR     = (205, 85,  55)
LOW_COLOR      = (65,  125, 200)
DIVIDER_COLOR  = (218, 212, 202)
ERROR_COLOR    = (200, 65,  65)
