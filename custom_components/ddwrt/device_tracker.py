"""Device tracker platform for DD-WRT."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.components.device_tracker import ScannerEntity, SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CONSIDER_HOME_ACTIVE,
    CONF_CONSIDER_HOME_DHCP,
    CONF_CONSIDER_HOME_WIFI,
    CONF_TRACK_ACTIVE,
    CONF_TRACK_DHCP,
    CONF_TRACK_WIFI,
    DEFAULT_CONSIDER_HOME_ACTIVE,
    DEFAULT_CONSIDER_HOME_DHCP,
    DEFAULT_CONSIDER_HOME_WIFI,
    DEFAULT_TRACK_ACTIVE,
    DEFAULT_TRACK_DHCP,
    DEFAULT_TRACK_WIFI,
    DOMAIN,
)
from .ddwrt_client import DDWRTData

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: DataUpdateCoordinator[DDWRTData] = hass.data[DOMAIN][entry.entry_id]

    _LOGGER.debug(
        "DD-WRT device_tracker setup: "
        "track_wifi=%s (consider_home=%ss) "
        "track_dhcp=%s (consider_home=%ss) "
        "track_active=%s (consider_home=%ss) "
        "wl_clients=%s dhcp_leases=%s active_clients=%s",
        entry.options.get(CONF_TRACK_WIFI, DEFAULT_TRACK_WIFI),
        entry.options.get(CONF_CONSIDER_HOME_WIFI, DEFAULT_CONSIDER_HOME_WIFI),
        entry.options.get(CONF_TRACK_DHCP, DEFAULT_TRACK_DHCP),
        entry.options.get(CONF_CONSIDER_HOME_DHCP, DEFAULT_CONSIDER_HOME_DHCP),
        entry.options.get(CONF_TRACK_ACTIVE, DEFAULT_TRACK_ACTIVE),
        entry.options.get(CONF_CONSIDER_HOME_ACTIVE, DEFAULT_CONSIDER_HOME_ACTIVE),
        len(coordinator.data.wl_clients) if coordinator.data else "NO DATA",
        len(coordinator.data.dhcp_leases) if coordinator.data else "NO DATA",
        len(coordinator.data.active_clients) if coordinator.data else "NO DATA",
    )

    wifi_tracked: set[str] = set()
    dhcp_tracked: set[str] = set()
    active_tracked: set[str] = set()

    @callback
    def _add_new_devices() -> None:
        if coordinator.data is None:
            _LOGGER.warning("DD-WRT device_tracker: coordinator.data is None — skipping")
            return

        new_entities: list[ScannerEntity] = []

        try:
            if entry.options.get(CONF_TRACK_WIFI, DEFAULT_TRACK_WIFI):
                for client in coordinator.data.wl_clients:
                    mac = client["mac"].upper()
                    if mac not in wifi_tracked:
                        wifi_tracked.add(mac)
                        new_entities.append(DDWRTWifiTracker(coordinator, entry, mac))

            if entry.options.get(CONF_TRACK_DHCP, DEFAULT_TRACK_DHCP):
                for lease in coordinator.data.dhcp_leases:
                    mac = lease["mac"].upper()
                    if mac not in dhcp_tracked:
                        dhcp_tracked.add(mac)
                        new_entities.append(DDWRTDhcpTracker(coordinator, entry, mac))

            if entry.options.get(CONF_TRACK_ACTIVE, DEFAULT_TRACK_ACTIVE):
                for client in coordinator.data.active_clients:
                    mac = client["mac"].upper()
                    if mac not in active_tracked:
                        active_tracked.add(mac)
                        new_entities.append(DDWRTActiveClientTracker(coordinator, entry, mac))

        except Exception:  # noqa: BLE001
            _LOGGER.exception("DD-WRT: unexpected error while building tracker entities")
            return

        if new_entities:
            _LOGGER.debug("DD-WRT device_tracker: adding %d new entities", len(new_entities))
            async_add_entities(new_entities)

    _add_new_devices()
    coordinator.async_add_listener(_add_new_devices)


class _ConsiderHomeMixin:
    """Grace-period mixin for DD-WRT tracker entities.

    Subclasses declare which options key holds their grace-period setting via
    the class attributes ``_consider_home_key`` and ``_consider_home_default``.
    On every coordinator update the mixin compares elapsed time since the
    device was last seen against the configured interval.  While the device is
    within the window it is still reported as "home"; once the window expires
    the entity flips to "away".  Reconnection within the window is invisible
    to HA — no spurious state changes.
    """

    # Subclasses must set these:
    _consider_home_key: str
    _consider_home_default: int

    # Set in subclass __init__:
    _entry: ConfigEntry
    _last_seen: datetime | None

    def _consider_home_seconds(self) -> int:
        return int(
            self._entry.options.get(self._consider_home_key, self._consider_home_default)
        )

    def _evaluate_connection(self, raw: bool) -> bool:
        """Return effective connected state with grace-period applied."""
        now = dt_util.utcnow()

        if raw:
            self._last_seen = now
            return True

        grace = self._consider_home_seconds()
        if grace > 0 and self._last_seen is not None:
            elapsed = (now - self._last_seen).total_seconds()
            if elapsed < grace:
                return True  # Still within grace window — stay home.

        return False

    def _consider_home_attributes(self) -> dict:
        """Attributes that expose grace-period state for debugging."""
        grace = self._consider_home_seconds()
        attrs: dict = {"consider_home_seconds": grace}
        if self._last_seen is not None:
            attrs["last_seen"] = self._last_seen.isoformat()
            if grace > 0:
                remaining = grace - (dt_util.utcnow() - self._last_seen).total_seconds()
                attrs["grace_remaining_seconds"] = max(0.0, round(remaining, 1))
        return attrs


class DDWRTWifiTracker(
    _ConsiderHomeMixin,
    CoordinatorEntity[DataUpdateCoordinator[DDWRTData]],
    ScannerEntity,
):
    """Tracks a device currently associated with the DD-WRT WiFi radio."""

    _attr_source_type = SourceType.ROUTER
    _consider_home_key = CONF_CONSIDER_HOME_WIFI
    _consider_home_default = DEFAULT_CONSIDER_HOME_WIFI

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[DDWRTData],
        entry: ConfigEntry,
        mac: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._mac = mac
        self._unique_id = f"{entry.entry_id}_wifi_{mac}"
        self._attr_name = f"[ddwrt-wifi] {mac}"
        self._last_seen: datetime | None = None

    @property
    def unique_id(self) -> str:
        return self._unique_id

    @property
    def entity_registry_enabled_default(self) -> bool:
        return True

    def _raw_is_connected(self) -> bool:
        if self.coordinator.data is None:
            return False
        return any(
            c["mac"].upper() == self._mac for c in self.coordinator.data.wl_clients
        )

    @property
    def is_connected(self) -> bool:
        return self._evaluate_connection(self._raw_is_connected())

    @property
    def mac_address(self) -> str:
        return self._mac

    @property
    def extra_state_attributes(self) -> dict:
        attrs = self._consider_home_attributes()
        attrs["tracker_type"] = "ddwrt-wifi"
        if self.coordinator.data is None:
            return attrs
        for client in self.coordinator.data.wl_clients:
            if client["mac"].upper() == self._mac:
                attrs.update(
                    {
                        "interface": client.get("interface"),
                        "signal": client.get("signal"),
                        "noise": client.get("noise"),
                        "snr": client.get("snr"),
                        "tx_rate": client.get("tx_rate"),
                        "rx_rate": client.get("rx_rate"),
                        "uptime": client.get("uptime"),
                    }
                )
                return attrs
        return attrs


class DDWRTDhcpTracker(
    _ConsiderHomeMixin,
    CoordinatorEntity[DataUpdateCoordinator[DDWRTData]],
    ScannerEntity,
):
    """Tracks a device with an active DHCP lease on DD-WRT."""

    _attr_source_type = SourceType.ROUTER
    _consider_home_key = CONF_CONSIDER_HOME_DHCP
    _consider_home_default = DEFAULT_CONSIDER_HOME_DHCP

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[DDWRTData],
        entry: ConfigEntry,
        mac: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._mac = mac
        self._unique_id = f"{entry.entry_id}_dhcp_{mac}"
        hostname = self._get_lease(coordinator.data, mac).get("hostname") or mac
        self._attr_name = f"[ddwrt-dhcp] {hostname}"
        self._last_seen: datetime | None = None

    @property
    def unique_id(self) -> str:
        return self._unique_id

    @property
    def entity_registry_enabled_default(self) -> bool:
        return True

    @staticmethod
    def _get_lease(data: DDWRTData | None, mac: str) -> dict:
        if data is None:
            return {}
        for lease in data.dhcp_leases:
            if lease["mac"].upper() == mac:
                return lease
        return {}

    def _raw_is_connected(self) -> bool:
        return bool(self._get_lease(self.coordinator.data, self._mac))

    @property
    def is_connected(self) -> bool:
        return self._evaluate_connection(self._raw_is_connected())

    @property
    def mac_address(self) -> str:
        return self._mac

    @property
    def ip_address(self) -> str | None:
        return self._get_lease(self.coordinator.data, self._mac).get("ip")

    @property
    def hostname(self) -> str | None:
        return self._get_lease(self.coordinator.data, self._mac).get("hostname")

    @property
    def extra_state_attributes(self) -> dict:
        lease = self._get_lease(self.coordinator.data, self._mac)
        attrs = self._consider_home_attributes()
        attrs.update(
            {
                "tracker_type": "ddwrt-dhcp",
                "ip": lease.get("ip"),
                "hostname": lease.get("hostname"),
                "expires": lease.get("expires"),
            }
        )
        return attrs


class DDWRTActiveClientTracker(
    _ConsiderHomeMixin,
    CoordinatorEntity[DataUpdateCoordinator[DDWRTData]],
    ScannerEntity,
):
    """Tracks a device visible in the DD-WRT ARP/active-clients table.

    Covers all LAN-connected devices (wired and wireless) that DD-WRT has in
    its ARP cache, regardless of DHCP lease or wireless association status.
    """

    _attr_source_type = SourceType.ROUTER
    _consider_home_key = CONF_CONSIDER_HOME_ACTIVE
    _consider_home_default = DEFAULT_CONSIDER_HOME_ACTIVE

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[DDWRTData],
        entry: ConfigEntry,
        mac: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._mac = mac
        self._unique_id = f"{entry.entry_id}_active_{mac}"
        hostname = self._get_client(coordinator.data, mac).get("hostname") or mac
        self._attr_name = f"[ddwrt-active] {hostname}"
        self._last_seen: datetime | None = None

    @property
    def unique_id(self) -> str:
        return self._unique_id

    @property
    def entity_registry_enabled_default(self) -> bool:
        return True

    @staticmethod
    def _get_client(data: DDWRTData | None, mac: str) -> dict:
        if data is None:
            return {}
        for client in data.active_clients:
            if client["mac"].upper() == mac:
                return client
        return {}

    def _raw_is_connected(self) -> bool:
        return bool(self._get_client(self.coordinator.data, self._mac))

    @property
    def is_connected(self) -> bool:
        return self._evaluate_connection(self._raw_is_connected())

    @property
    def mac_address(self) -> str:
        return self._mac

    @property
    def ip_address(self) -> str | None:
        return self._get_client(self.coordinator.data, self._mac).get("ip")

    @property
    def hostname(self) -> str | None:
        return self._get_client(self.coordinator.data, self._mac).get("hostname")

    @property
    def extra_state_attributes(self) -> dict:
        client = self._get_client(self.coordinator.data, self._mac)
        attrs = self._consider_home_attributes()
        attrs.update(
            {
                "tracker_type": "ddwrt-active",
                "ip": client.get("ip"),
                "hostname": client.get("hostname"),
            }
        )
        return attrs
