"""Config flow for the Doremi DCP-2000."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import DCP2000Client, DCPError, DeviceInfo, probe_klv
from .const import (
    CONF_DAY_START,
    CONF_EXTERNAL_PLAYERS,
    CONF_HISTORY_DAYS,
    CONF_PAUSE_LIMIT,
    CONF_SCHEDULE,
    CONF_SCHEDULE_DAYS,
    CONF_SCHEDULE_INTERVAL,
    CONF_SOAP_PASSWORD,
    CONF_SOAP_PORT,
    CONF_SOAP_USER,
    DEFAULT_DAY_START,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_PAUSE_LIMIT,
    DEFAULT_SCHEDULE_DAYS,
    DEFAULT_SCHEDULE_INTERVAL,
    DEFAULT_SOAP_PASSWORD,
    DEFAULT_SOAP_PORT,
    DEFAULT_SOAP_USER,
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
from .soap import DoremiSoapClient, SoapAuthError, SoapError

_LOGGER = logging.getLogger(__name__)


async def _validate(host: str, port: int) -> DeviceInfo:
    client = DCP2000Client(host, port)
    try:
        return await client.device_info()
    finally:
        await client.close()


async def _validate_soap(hass: HomeAssistant, host: str, port: int, user: str, password: str) -> str | None:
    """Read-only test of the web services: Login + GetSchedulerStatus. Returns an error key."""
    client = DoremiSoapClient(async_get_clientsession(hass), host, port, user, password)
    try:
        await client.login()
        await client.scheduler_status()
    except SoapAuthError:
        return "soap_auth"
    except SoapError as err:
        _LOGGER.debug("Web services of %s not usable: %s", host, err)
        return "soap_failed"
    finally:
        await client.logout()
    return None


def _password_selector() -> selector.TextSelector:
    return selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))


def _unique_id(info: DeviceInfo, host: str) -> str:
    return info.serial.strip() if info.serial.strip() else host


class DCP2000ConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(entry: DCPConfigEntry) -> OptionsFlowWithReload:
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
                if user_input.get(CONF_SCHEDULE):
                    error = await _validate_soap(
                        self.hass, host, DEFAULT_SOAP_PORT,
                        user_input[CONF_SOAP_USER], user_input[CONF_SOAP_PASSWORD],
                    )
                    if error:
                        errors["base"] = error
                    else:
                        options.update(
                            {
                                CONF_SCHEDULE: True,
                                CONF_SOAP_PORT: DEFAULT_SOAP_PORT,
                                CONF_SOAP_USER: user_input[CONF_SOAP_USER],
                                CONF_SOAP_PASSWORD: user_input[CONF_SOAP_PASSWORD],
                            }
                        )
                if not errors:
                    return self.async_create_entry(
                        title=name, data={CONF_HOST: host, CONF_PORT: port}, options=options
                    )
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
                vol.Optional(CONF_NAME): str,
                vol.Required(CONF_SCHEDULE, default=True): bool,
                vol.Required(CONF_SOAP_USER, default=DEFAULT_SOAP_USER): str,
                vol.Required(CONF_SOAP_PASSWORD, default=DEFAULT_SOAP_PASSWORD): _password_selector(),
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


    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Web-services login was refused: ask for user and password again."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await _validate_soap(
                self.hass, entry.data[CONF_HOST],
                entry.options.get(CONF_SOAP_PORT, DEFAULT_SOAP_PORT),
                user_input[CONF_SOAP_USER], user_input[CONF_SOAP_PASSWORD],
            )
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry, options={**entry.options, **user_input}
                )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SOAP_USER, default=entry.options.get(CONF_SOAP_USER, DEFAULT_SOAP_USER)
                ): str,
                vol.Required(CONF_SOAP_PASSWORD): _password_selector(),
            }
        )
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors=errors)


class DCP2000OptionsFlow(OptionsFlowWithReload):
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
            if data.get(CONF_SCHEDULE) and not errors:
                error = await _validate_soap(
                    self.hass, self.config_entry.data[CONF_HOST], data[CONF_SOAP_PORT],
                    data[CONF_SOAP_USER], data[CONF_SOAP_PASSWORD],
                )
                if error:
                    errors[CONF_SCHEDULE] = error
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
                # --- show schedule (web services, read only) ---
                vol.Required(CONF_SCHEDULE, default=opts.get(CONF_SCHEDULE, False)): bool,
                vol.Required(
                    CONF_SOAP_PORT, default=opts.get(CONF_SOAP_PORT, DEFAULT_SOAP_PORT)
                ): vol.Coerce(int),
                vol.Required(
                    CONF_SOAP_USER, default=opts.get(CONF_SOAP_USER, DEFAULT_SOAP_USER)
                ): str,
                vol.Required(
                    CONF_SOAP_PASSWORD, default=opts.get(CONF_SOAP_PASSWORD, DEFAULT_SOAP_PASSWORD)
                ): _password_selector(),
                vol.Required(
                    CONF_SCHEDULE_INTERVAL,
                    default=opts.get(CONF_SCHEDULE_INTERVAL, DEFAULT_SCHEDULE_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
                vol.Required(
                    CONF_SCHEDULE_DAYS, default=opts.get(CONF_SCHEDULE_DAYS, DEFAULT_SCHEDULE_DAYS)
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
                vol.Required(
                    CONF_HISTORY_DAYS, default=opts.get(CONF_HISTORY_DAYS, DEFAULT_HISTORY_DAYS)
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=1000)),
                vol.Required(
                    CONF_DAY_START, default=opts.get(CONF_DAY_START, DEFAULT_DAY_START)
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=12)),
                vol.Required(
                    CONF_PAUSE_LIMIT, default=opts.get(CONF_PAUSE_LIMIT, DEFAULT_PAUSE_LIMIT)
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=1440)),
                vol.Optional(
                    CONF_EXTERNAL_PLAYERS, default=opts.get(CONF_EXTERNAL_PLAYERS, [])
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["media_player", "sensor"], multiple=True)
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )
