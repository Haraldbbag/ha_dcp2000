"""Config flow for the Doremi DCP-2000."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback

from .client import DCP2000Client, DCPError, DeviceInfo, probe_klv
from .const import (
    CONF_FPS,
    CONF_GPIO,
    CONF_KLV,
    CONF_KLV_PORT,
    CONF_KLV_REG,
    CONF_SCAN_INTERVAL,
    DEFAULT_FPS,
    DEFAULT_KLV_PORT,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import DCPConfigEntry

_LOGGER = logging.getLogger(__name__)


async def _validate(host: str, port: int) -> DeviceInfo:
    client = DCP2000Client(host, port)
    try:
        return await client.device_info()
    finally:
        await client.close()


def _unique_id(info: DeviceInfo, host: str) -> str:
    return info.serial.strip() if info.serial.strip() else host


class DCP2000ConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(entry: DCPConfigEntry) -> OptionsFlow:
        return DCP2000OptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            self._async_abort_entries_match({CONF_HOST: host})
            try:
                info = await _validate(host, port)
            except DCPError as err:
                _LOGGER.debug("Cannot connect to %s: %s", host, err)
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(_unique_id(info, host))
                self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_PORT: port})
                name = user_input.get(CONF_NAME) or f"{info.product or 'DCP-2000'} {host}"
                # Enable the KLV API (exact frame rates, SPL control) when it answers.
                options: dict[str, Any] = {}
                reg = await probe_klv(host, DEFAULT_KLV_PORT)
                if reg is not None:
                    options = {CONF_KLV: True, CONF_KLV_PORT: DEFAULT_KLV_PORT, CONF_KLV_REG: reg}
                return self.async_create_entry(
                    title=name, data={CONF_HOST: host, CONF_PORT: port}, options=options
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
                vol.Optional(CONF_NAME): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                info = await _validate(host, user_input[CONF_PORT])
            except DCPError:
                errors["base"] = "cannot_connect"
            else:
                if entry.unique_id != entry.data[CONF_HOST]:
                    await self.async_set_unique_id(_unique_id(info, host))
                    self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_HOST: host, CONF_PORT: user_input[CONF_PORT]}
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str,
                vol.Required(CONF_PORT, default=entry.data.get(CONF_PORT, DEFAULT_PORT)): vol.Coerce(int),
            }
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)


class DCP2000OptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        opts = self.config_entry.options
        if user_input is not None:
            data = dict(user_input)
            if data.get(CONF_KLV):
                reg = await probe_klv(self.config_entry.data[CONF_HOST], data[CONF_KLV_PORT])
                if reg is None:
                    errors[CONF_KLV] = "klv_unreachable"
                else:
                    data[CONF_KLV_REG] = reg
            if not errors:
                return self.async_create_entry(data=data)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL, default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
                vol.Required(CONF_FPS, default=opts.get(CONF_FPS, DEFAULT_FPS)): vol.All(
                    vol.Coerce(float), vol.Range(min=1, max=120)
                ),
                vol.Required(CONF_GPIO, default=opts.get(CONF_GPIO, False)): bool,
                vol.Required(CONF_KLV, default=opts.get(CONF_KLV, False)): bool,
                vol.Required(
                    CONF_KLV_PORT, default=opts.get(CONF_KLV_PORT, DEFAULT_KLV_PORT)
                ): vol.Coerce(int),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )
