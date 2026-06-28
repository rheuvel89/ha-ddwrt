"""Device tracker platform for DD-WRT."""
from __future__ import annotations

import logging

from homeassistant.components.device_tracker import ScannerEntity, SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import (
    CONF_TRACK_DHCP,
    CONF_TRACK_WIFI,
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
        "DD-WRT device_tracker setup: track_wifi=%s track_dhcp=%s "
        "wl_clients=%s dhcp_leases=%s",
        entry.options.get(CONF_TRACK_WIFI, DEFAULT_TRACK_WIFI),
        entry.options.get(CONF_TRACK_DHCP, DEFAULT_TRACK_DHCP),
        len(coordinator.data.wl_clients) if coordinator.data else "NO DATA",
        len(coordinator.data.dhcp_leases) if coordinator.data else "NO DATA",
    )

    wifi_tracked: set[str] = set()
    dhcp_tracked: set[str] = set()

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

        except Exception:  # noqa: BLE001
            _LOGGER.exception("DD-WRT: unexpected error while building tracker entities")
            return

        if new_entities:
            _LOGGER.debug("DD-WRT device_tracker: adding %d new entities", len(new_entities))
            async_add_entities(new_entities)

    _add_new_devices()
    coordinator.async_add_listener(_add_new_devices)


class DDWRTWifiTracker(
    CoordinatorEntity[DataUpdateCoordinator[DDWRTData]], ScannerEntity
):
    """Tracks a device currently associated with the DD-WRT WiFi radio."""

    _attr_source_type = SourceType.ROUTER

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[DDWRTData],
        entry: ConfigEntry,
        mac: str,
    ) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._unique_id = f"{entry.entry_id}_wifi_{mac}"
        self._attr_name = f"[ddwrt-wifi] {mac}"

    @property
    def unique_id(self) -> str:
        """Return a unique ID — must override ScannerEntity which returns mac_address."""
        return self._unique_id

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Enable by default — ScannerEntity disables until a device entry exists."""
        return True

    @property
    def is_connected(self) -> bool:
        if self.coordinator.data is None:
            return False
        return any(
            c["mac"].upper() == self._mac
            for c in self.coordinator.data.wl_clients
        )

    @property
    def mac_address(self) -> str:
        return self._mac

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {"tracker_type": "ddwrt-wifi"}
        for client in self.coordinator.data.wl_clients:
            if client["mac"].upper() == self._mac:
                return {
                    "tracker_type": "ddwrt-wifi",
                    "interface": client.get("interface"),
                    "signal": client.get("signal"),
                    "noise": client.get("noise"),
                    "snr": client.get("snr"),
                    "tx_rate": client.get("tx_rate"),
                    "rx_rate": client.get("rx_rate"),
                    "uptime": client.get("uptime"),
                }
        return {"tracker_type": "ddwrt-wifi"}


class DDWRTDhcpTracker(
    CoordinatorEntity[DataUpdateCoordinator[DDWRTData]], ScannerEntity
):
    """Tracks a device with an active DHCP lease on DD-WRT."""

    _attr_source_type = SourceType.ROUTER

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[DDWRTData],
        entry: ConfigEntry,
        mac: str,
    ) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._unique_id = f"{entry.entry_id}_dhcp_{mac}"
        hostname = self._get_lease(coordinator.data, mac).get("hostname") or mac
        self._attr_name = f"[ddwrt-dhcp] {hostname}"

    @property
    def unique_id(self) -> str:
        """Return a unique ID — must override ScannerEntity which returns mac_address."""
        return self._unique_id

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Enable by default — ScannerEntity disables until a device entry exists."""
        return True

    @staticmethod
    def _get_lease(data: DDWRTData | None, mac: str) -> dict:
        if data is None:
            return {}
        for lease in data.dhcp_leases:
            if lease["mac"].upper() == mac:
                return lease
        return {}

    @property
    def is_connected(self) -> bool:
        return bool(self._get_lease(self.coordinator.data, self._mac))

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
        return {
            "tracker_type": "ddwrt-dhcp",
            "ip": lease.get("ip"),
            "hostname": lease.get("hostname"),
            "expires": lease.get("expires"),
        }
