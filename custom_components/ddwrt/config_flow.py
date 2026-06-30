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

_CONSIDER_HOME_FIELD = vol.All(vol.Coerce(int), vol.Range(min=0, max=3600))


def _tracker_toggles_schema(current: dict[str, Any]) -> vol.Schema:
    """Schema for step 1: which tracker families to enable."""
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


def _consider_home_schema(
    tracker_choices: dict[str, Any],
    current: dict[str, Any],
) -> vol.Schema:
    """Schema for step 2: grace-period fields for each *enabled* tracker.

    Only fields whose corresponding tracker toggle is True are included, so
    the form only shows options that are actually relevant.
    """
    fields: dict = {}
    if tracker_choices.get(CONF_TRACK_WIFI, DEFAULT_TRACK_WIFI):
        fields[
            vol.Optional(
                CONF_CONSIDER_HOME_WIFI,
                default=current.get(CONF_CONSIDER_HOME_WIFI, DEFAULT_CONSIDER_HOME_WIFI),
            )
        ] = _CONSIDER_HOME_FIELD
    if tracker_choices.get(CONF_TRACK_DHCP, DEFAULT_TRACK_DHCP):
        fields[
            vol.Optional(
                CONF_CONSIDER_HOME_DHCP,
                default=current.get(CONF_CONSIDER_HOME_DHCP, DEFAULT_CONSIDER_HOME_DHCP),
            )
        ] = _CONSIDER_HOME_FIELD
    if tracker_choices.get(CONF_TRACK_ACTIVE, DEFAULT_TRACK_ACTIVE):
        fields[
            vol.Optional(
                CONF_CONSIDER_HOME_ACTIVE,
                default=current.get(CONF_CONSIDER_HOME_ACTIVE, DEFAULT_CONSIDER_HOME_ACTIVE),
            )
        ] = _CONSIDER_HOME_FIELD
    return vol.Schema(fields)


def _any_tracker_enabled(choices: dict[str, Any]) -> bool:
    return any(
        [
            choices.get(CONF_TRACK_WIFI, DEFAULT_TRACK_WIFI),
            choices.get(CONF_TRACK_DHCP, DEFAULT_TRACK_DHCP),
            choices.get(CONF_TRACK_ACTIVE, DEFAULT_TRACK_ACTIVE),
        ]
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
    """Handle a config flow for DD-WRT.

    Steps
    -----
    1. user           — connection details
    2. trackers       — which tracker families to enable
    3. tracker_options — per-tracker consider-home grace periods
                         (only shown when at least one tracker is enabled)
    """

    VERSION = 1

    def __init__(self) -> None:
        self._connection_data: dict[str, Any] = {}
        self._title: str = ""
        self._tracker_choices: dict[str, Any] = {}

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
        """Step 2: choose which tracker families to enable."""
        if user_input is not None:
            self._tracker_choices = user_input
            return await self.async_step_tracker_options()

        return self.async_show_form(
            step_id="trackers",
            data_schema=_tracker_toggles_schema({}),
        )

    async def async_step_tracker_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3: consider-home grace periods for enabled trackers."""
        if user_input is not None:
            options = {**self._tracker_choices, **user_input}
            return self.async_create_entry(
                title=self._title,
                data=self._connection_data,
                options=options,
            )

        # Skip this step if no trackers are enabled.
        if not _any_tracker_enabled(self._tracker_choices):
            return self.async_create_entry(
                title=self._title,
                data=self._connection_data,
                options=self._tracker_choices,
            )

        return self.async_show_form(
            step_id="tracker_options",
            data_schema=_consider_home_schema(self._tracker_choices, {}),
        )


class OptionsFlow(config_entries.OptionsFlow):
    """Handle DD-WRT options.

    Steps
    -----
    1. init            — which tracker families to enable
    2. tracker_options — per-tracker consider-home grace periods
                         (only shown when at least one tracker is enabled)
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self._tracker_choices: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: tracker toggles."""
        if user_input is not None:
            self._tracker_choices = user_input
            return await self.async_step_tracker_options()

        return self.async_show_form(
            step_id="init",
            data_schema=_tracker_toggles_schema(self._config_entry.options),
        )

    async def async_step_tracker_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: consider-home grace periods for enabled trackers."""
        if user_input is not None:
            # Merge: keep existing values (including consider_home for disabled
            # trackers so they're remembered if re-enabled later), apply new
            # toggle states, then apply new grace-period values.
            options = {
                **self._config_entry.options,
                **self._tracker_choices,
                **user_input,
            }
            return self.async_create_entry(title="", data=options)

        # Skip this step if no trackers are enabled.
        if not _any_tracker_enabled(self._tracker_choices):
            options = {**self._config_entry.options, **self._tracker_choices}
            return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="tracker_options",
            data_schema=_consider_home_schema(
                self._tracker_choices, self._config_entry.options
            ),
        )
