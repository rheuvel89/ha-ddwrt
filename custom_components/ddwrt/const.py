"""Constants for the DD-WRT integration."""

DOMAIN = "ddwrt"
DEFAULT_SCAN_INTERVAL = 30  # seconds

PLATFORMS = ["sensor", "binary_sensor", "device_tracker"]

# Options flow keys — tracker toggles
CONF_TRACK_WIFI = "track_wifi_clients"
CONF_TRACK_DHCP = "track_dhcp_clients"
CONF_TRACK_ACTIVE = "track_active_clients"

# Options flow keys — per-tracker consider-home grace periods (seconds)
CONF_CONSIDER_HOME_WIFI = "consider_home_wifi"
CONF_CONSIDER_HOME_DHCP = "consider_home_dhcp"
CONF_CONSIDER_HOME_ACTIVE = "consider_home_active"

# Defaults
DEFAULT_TRACK_WIFI = True
DEFAULT_TRACK_DHCP = True
DEFAULT_TRACK_ACTIVE = True
DEFAULT_CONSIDER_HOME_WIFI = 0    # 0 = disabled (report away immediately)
DEFAULT_CONSIDER_HOME_DHCP = 0
DEFAULT_CONSIDER_HOME_ACTIVE = 0
