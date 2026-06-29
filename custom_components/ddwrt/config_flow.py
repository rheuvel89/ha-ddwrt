"""Config flow for DD-WRT integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.aiohttp_client as ha_aiohttp

from .const import (
    CONF_TRACK_ACTIVE,
    CONF_TRACK_DHCP,
    CONF_TRACK_WIFI,
    DEFAULT_TRACK_ACTIVE,
    DEFAULT_TRACK_DHCP,
    DEFAULT_TRACK_WIFI,
    DOMAIN,
)
from .ddwrt_client import AuthError, DDWRTClient

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=80): int,
        vol.Required(CONF_USERNAME, default="root"): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_SSL, default=False): bool,
    }
)


def _trackers_schema(current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_TRACK_WIFI,
                default=current.get(CONF_TRACK_WIFI, DEFAULT_TRACK_WIFI),
            ): bool,
            vol.Required(
                CONF_TRACK_DHCP,
                default=current.get(CONF_TRACK_DHCP, DEFAULT_TRACK_DHCP),
            ): bool,
            vol.Required(
                CONF_TRACK_ACTIVE,
                default=current.get(CONF_TRACK_ACTIVE, DEFAULT_TRACK_ACTIVE),
            ): bool,
        }
    )


async def _validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Validate credentials and return router info."""
    _LOGGER.debug(
        "DD-WRT setup: host=%s port=%s username=%s password_length=%d",
        data.get(CONF_HOST),
        data.get(CONF_PORT),
        data.get(CONF_USERNAME),
        len(data.get(CONF_PASSWORD, "")),
    )
    client = DDWRTClient(
        host=data[CONF_HOST],
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        port=data[CONF_PORT],
        ssl=data[CONF_SSL],
    )
    try:
        router_data = await client.async_get_data()
    except AuthError as err:
        _LOGGER.error("DD-WRT auth error: %s", err)
        raise InvalidAuth from err
    except ConnectionError as err:
        _LOGGER.error("DD-WRT connection error during setup: %s", err)
        raise CannotConnect from err
    finally:
        await client.close()
    return {"title": router_data.router_name or data[CONF_HOST]}


class CannotConnect(Exception):
    """Raised when we can't connect to the router."""


class InvalidAuth(Exception):
    """Raised when credentials are rejected."""


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for DD-WRT."""

    VERSION = 1

    def __init__(self) -> None:
        self._connection_data: dict[str, Any] = {}
        self._title: str = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> OptionsFlow:
        return OptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: connection details."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await _validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during DD-WRT setup")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(user_input[CONF_HOST])
                self._abort_if_unique_id_configured()
                self._connection_data = user_input
                self._title = info["title"]
                return await self.async_step_trackers()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_trackers(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: choose which device tracker families to enable."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._title,
                data=self._connection_data,
                options=user_input,
            )

        return self.async_show_form(
            step_id="trackers",
            data_schema=_trackers_schema({}),
        )


class OptionsFlow(config_entries.OptionsFlow):
    """Handle DD-WRT options (tracker toggles)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=_trackers_schema(self._config_entry.options),
        )
