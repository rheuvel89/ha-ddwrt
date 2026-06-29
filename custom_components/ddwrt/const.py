"""Constants for the DD-WRT integration."""

DOMAIN = "ddwrt"
DEFAULT_SCAN_INTERVAL = 30  # seconds

PLATFORMS = ["sensor", "binary_sensor", "device_tracker"]

# Options flow keys
CONF_TRACK_WIFI = "track_wifi_clients"
CONF_TRACK_DHCP = "track_dhcp_clients"
CONF_TRACK_ACTIVE = "track_active_clients"

# Defaults
DEFAULT_TRACK_WIFI = True
DEFAULT_TRACK_DHCP = True
DEFAULT_TRACK_ACTIVE = True
