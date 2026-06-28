"""Sensor platform for DD-WRT."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN
from .ddwrt_client import DDWRTData


@dataclass
class DDWRTSensorEntityDescription(SensorEntityDescription):
    """Extend standard description with a value getter."""

    value_fn: Callable[[DDWRTData], Any] = lambda _: None


SENSORS: tuple[DDWRTSensorEntityDescription, ...] = (
    DDWRTSensorEntityDescription(
        key="wan_ipaddr",
        name="WAN IP Address",
        icon="mdi:ip-network",
        value_fn=lambda d: d.wan_ipaddr or None,
    ),
    DDWRTSensorEntityDescription(
        key="wan_status",
        name="WAN Status",
        icon="mdi:wan",
        value_fn=lambda d: d.wan_status or None,
    ),
    DDWRTSensorEntityDescription(
        key="wan_proto",
        name="WAN Protocol",
        icon="mdi:protocol",
        value_fn=lambda d: d.wan_proto or None,
    ),
    DDWRTSensorEntityDescription(
        key="uptime",
        name="Uptime",
        icon="mdi:clock-outline",
        value_fn=lambda d: d.uptime or None,
    ),
    DDWRTSensorEntityDescription(
        key="load_avg",
        name="Load Average",
        icon="mdi:gauge",
        # load_avg is a free-form string like "0.10 0.05 0.02"; no state_class
        # because HA rejects non-numeric values for MEASUREMENT sensors.
        value_fn=lambda d: d.load_avg or None,
    ),
    DDWRTSensorEntityDescription(
        key="mem_used",
        name="Memory Used",
        icon="mdi:memory",
        native_unit_of_measurement=UnitOfInformation.KILOBYTES,
        state_class=SensorStateClass.MEASUREMENT,
        # Use `is not None` guard so that a legitimate value of 0 is reported
        # as 0 rather than as unknown.
        value_fn=lambda d: d.mem_used if d.mem_used is not None else None,
    ),
    DDWRTSensorEntityDescription(
        key="mem_free",
        name="Memory Free",
        icon="mdi:memory",
        native_unit_of_measurement=UnitOfInformation.KILOBYTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.mem_free if d.mem_free is not None else None,
    ),
    DDWRTSensorEntityDescription(
        key="mem_usage_pct",
        name="Memory Usage",
        icon="mdi:memory",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: (
            round(d.mem_used / d.mem_total * 100, 1) if d.mem_total else None
        ),
    ),
    DDWRTSensorEntityDescription(
        key="wl_ssid",
        name="WiFi SSID",
        icon="mdi:wifi",
        value_fn=lambda d: d.wl_ssid or None,
    ),
    DDWRTSensorEntityDescription(
        key="wl_channel",
        name="WiFi Channel",
        icon="mdi:wifi-settings",
        value_fn=lambda d: d.wl_channel or None,
    ),
    DDWRTSensorEntityDescription(
        key="wl_rate",
        name="WiFi TX Rate",
        icon="mdi:wifi-arrow-up-down",
        value_fn=lambda d: d.wl_rate or None,
    ),
    DDWRTSensorEntityDescription(
        key="wl_clients",
        name="WiFi Clients",
        icon="mdi:devices",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.wl_clients),
    ),
    DDWRTSensorEntityDescription(
        key="dhcp_leases",
        name="DHCP Leases",
        icon="mdi:lan",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.dhcp_leases),
    ),
    DDWRTSensorEntityDescription(
        key="lan_ipaddr",
        name="LAN IP Address",
        icon="mdi:ip-network-outline",
        value_fn=lambda d: d.lan_ipaddr or None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: DataUpdateCoordinator[DDWRTData] = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        DDWRTSensor(coordinator, entry, description) for description in SENSORS
    )


class DDWRTSensor(CoordinatorEntity[DataUpdateCoordinator[DDWRTData]], SensorEntity):
    """A single DD-WRT sensor."""

    entity_description: DDWRTSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[DDWRTData],
        entry: ConfigEntry,
        description: DDWRTSensorEntityDescription,
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
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)
