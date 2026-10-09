# Copy to config_local.py (git-ignored) and fill in. Overrides values from config.py.
# This repository is public: everything personal belongs in config_local.py, not in config.py.

# Where you live (weather and rain radar), e.g. from the coordinates of your address.
LATITUDE = 52.0000
LONGITUDE = 5.0000
LOCATION_NAME = "Home"

# Waste calendar (Ximmio, used by many Dutch collectors such as Blink).
# The company code is in the web address of your collector's calendar page (companyCode=...).
WASTE_COMPANY_CODE = ""
WASTE_POSTCODE = "1234AB"
WASTE_HOUSENUMBER = "1"

# Garage door: a (read-only) user on the Home Assistant MQTT broker.
# Create one in Home Assistant under Settings > People > Users.
GARAGE_MQTT_USER = ""
GARAGE_MQTT_PASSWORD = ""

# Home Assistant: a long-lived access token (profile > Security > Long-lived access tokens).
HA_TOKEN = ""
