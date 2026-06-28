"""Device tracker platform for DD-WRT.

Two separate tracker families:
  - WiFi trackers  (from /Status_Wireless.live.asp active_wireless)
  - DHCP trackers  (from /Status_Lan.live.asp dhcp_leases)

Each family can be independently toggled via the integration's Options flow
(Settings → Devices & Services → DD-WRT → Configure).
"""
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

    track_wifi: bool = entry.options.get(CONF_TRACK_WIFI, DEFAULT_TRACK_WIFI)
    track_dhcp: bool = entry.options.get(CONF_TRACK_DHCP, DEFAULT_TRACK_DHCP)

    _LOGGER.debug(
        "DD-WRT device_tracker setup: track_wifi=%s track_dhcp=%s "
        "coordinator_has_data=%s wl_clients=%d dhcp_leases=%d",
        track_wifi, track_dhcp,
        coordinator.data is not None,
        len(coordinator.data.wl_clients) if coordinator.data else -1,
        len(coordinator.data.dhcp_leases) if coordinator.data else -1,
    )

    wifi_tracked: set[str] = set()
    dhcp_tracked: set[str] = set()

    @callback
    def _add_new_devices() -> None:
        # Guard: coordinator might not have data yet on very first call
        if coordinator.data is None:
            _LOGGER.warning("DD-WRT device_tracker: coordinator.data is None — skipping")
            return

        new_entities: list[ScannerEntity] = []

        try:
            # ── WiFi clients ────────────────────────────────────────────────
            if entry.options.get(CONF_TRACK_WIFI, DEFAULT_TRACK_WIFI):
                for client in coordinator.data.wl_clients:
                    mac = client["mac"].upper()
                    if mac not in wifi_tracked:
                        wifi_tracked.add(mac)
                        new_entities.append(
                            DDWRTWifiTracker(coordinator, entry, mac)
                        )

            # ── DHCP leases ─────────────────────────────────────────────────
            if entry.options.get(CONF_TRACK_DHCP, DEFAULT_TRACK_DHCP):
                for lease in coordinator.data.dhcp_leases:
                    mac = lease["mac"].upper()
                    if mac not in dhcp_tracked:
                        dhcp_tracked.add(mac)
                        new_entities.append(
                            DDWRTDhcpTracker(coordinator, entry, mac)
                        )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("DD-WRT: unexpected error while building tracker entities")
            return

        _LOGGER.debug(
            "DD-WRT device_tracker: adding %d new entities "
            "(wifi_total=%d, dhcp_total=%d)",
            len(new_entities), len(wifi_tracked), len(dhcp_tracked),
        )
        if new_entities:
            async_add_entities(new_entities)

    _add_new_devices()
    coordinator.async_add_listener(_add_new_devices)


# ─────────────────────────────────────────────────────────────────────────────
# WiFi tracker
# ─────────────────────────────────────────────────────────────────────────────

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
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{entry.entry_id}_wifi_{mac}"
        self._attr_name = f"WiFi {mac}"

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
            return {"tracker_type": "wifi"}
        for client in self.coordinator.data.wl_clients:
            if client["mac"].upper() == self._mac:
                return {
                    "tracker_type": "wifi",
                    "interface": client.get("interface"),
                    "signal": client.get("signal"),
                    "noise": client.get("noise"),
                    "snr": client.get("snr"),
                    "tx_rate": client.get("tx_rate"),
                    "rx_rate": client.get("rx_rate"),
                    "uptime": client.get("uptime"),
                }
        return {"tracker_type": "wifi"}


# ─────────────────────────────────────────────────────────────────────────────
# DHCP tracker
# ─────────────────────────────────────────────────────────────────────────────

class DDWRTDhcpTracker(
    CoordinatorEntity[DataUpdateCoordinator[DDWRTData]], ScannerEntity
):
    """Tracks a device with an active DHCP lease on DD-WRT.

    'Connected' means the lease is still present in the lease table.
    This covers both wired and wireless clients.
    """

    _attr_source_type = SourceType.ROUTER

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[DDWRTData],
        entry: ConfigEntry,
        mac: str,
    ) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_unique_id = f"{entry.entry_id}_dhcp_{mac}"
        # Use hostname from lease as the initial name; HA users can rename later
        hostname = self._get_lease(coordinator.data, mac).get("hostname") or mac
        self._attr_name = f"DHCP {hostname}"

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
        """True while the DHCP lease exists in the router's table."""
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
            "tracker_type": "dhcp",
            "ip": lease.get("ip"),
            "hostname": lease.get("hostname"),
            "expires": lease.get("expires"),
        }
