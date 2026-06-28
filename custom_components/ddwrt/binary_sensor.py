"""Binary sensor platform for DD-WRT."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN
from .ddwrt_client import DDWRTData


@dataclass
class DDWRTBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[DDWRTData], bool] = lambda _: False


BINARY_SENSORS: tuple[DDWRTBinarySensorDescription, ...] = (
    DDWRTBinarySensorDescription(
        key="wan_connected",
        name="WAN Connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        icon="mdi:wan",
        # DD-WRT WAN connected logic:
        #
        # For *static* WAN (no DHCP handshake needed), the router is connected
        # as long as it has a WAN IP — the wan_status string may say "Error"
        # because DD-WRT uses that to signal "no dynamic connection attempt was
        # made", not that the link is down.
        #
        # For all other protocols (dhcp, pppoe, pptp, etc.) we trust wan_status
        # which is normalised by _strip_html() in the client.
        value_fn=lambda d: (
            bool(d.wan_ipaddr)  # static: IP present = link up
            if d.wan_proto.lower() == "static"
            else d.wan_status.lower() in ("connected", "1", "true")
        ),
    ),
    DDWRTBinarySensorDescription(
        key="wl_radio",
        name="WiFi Radio",
        device_class=BinarySensorDeviceClass.POWER,
        icon="mdi:wifi",
        # _resolve_radio() in ddwrt_client normalises the value to "Enabled" or
        # "Disabled" (or infers state from SSID/clients when the key is absent),
        # so a simple equality check is sufficient here.
        value_fn=lambda d: d.wl_radio.lower() in ("enabled", "on", "1", "true"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: DataUpdateCoordinator[DDWRTData] = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        DDWRTBinarySensor(coordinator, entry, desc) for desc in BINARY_SENSORS
    )


class DDWRTBinarySensor(
    CoordinatorEntity[DataUpdateCoordinator[DDWRTData]], BinarySensorEntity
):
    """A DD-WRT binary sensor."""

    entity_description: DDWRTBinarySensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[DDWRTData],
        entry: ConfigEntry,
        description: DDWRTBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": coordinator.data.router_name or "DD-WRT Router",
            "manufacturer": "DD-WRT",
            "model": "Router",
        }

    @property
    def is_on(self) -> bool:
        return self.entity_description.value_fn(self.coordinator.data)
