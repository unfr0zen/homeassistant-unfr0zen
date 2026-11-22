"""Constants for the unfr0zen schedule integration."""

DOMAIN = "unfr0zen"

CONF_TRANSPORT = "transport"
CONF_BASE = "base"
CONF_NAME = "friendly_name"
CONF_IEEE = "ieee"
CONF_DEVICE = "device_id"

NESTLOCAL = "nestlocal"
Z2M = "zigbee2mqtt"
ZHA = "zha"
MATTER = "matter"

# What nestlocal tells a Zigbee coordinator and a Matter controller.
MANUFACTURER = "Google Nest"
MODEL = "Nest Learning Thermostat"

# The setpoints each transport takes, °C: nestlocal's own topics take the
# unit's whole range; Zigbee and Matter the thermostat's.
TEMP_LIMITS = {NESTLOCAL: (4.5, 35.0)}
ZIGBEE_MATTER_LIMITS = (9.0, 32.0)
TEMP_STEP = 0.5

# What the cards load, served from the integration.
# The cards and their locales, served from frontend/.
FRONTEND_URL = "/unfr0zen"
CARDS_URL = f"{FRONTEND_URL}/unfr0zen-cards.js"
