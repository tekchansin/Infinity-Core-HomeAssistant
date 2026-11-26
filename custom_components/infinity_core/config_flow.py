"""Add an Infinity Core: found on the network (DNS-SD), or by its address."""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DEFAULT_PORT, DOMAIN

try:  # HA 2025+
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
except ImportError:  # older
    from homeassistant.components.zeroconf import ZeroconfServiceInfo


class InfinityCoreFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self.found: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host, port = user_input[CONF_HOST].strip(), user_input[CONF_PORT]
            try:  # SPEC §2.1: the Core's id + name, so the entry has the same unique id as one found by zeroconf
                session = async_get_clientsession(self.hass, verify_ssl=False)  # self-signed; the SDK pins the key
                async with session.get(f"https://{host}:{port}/lan/hello", timeout=aiohttp.ClientTimeout(total=10)) as r:
                    hello = await r.json(content_type=None)
                core_id = str(hello["id"])
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError):
                errors["base"] = "cannot_connect"
            else:
                if str(hello.get("api", "")) != "1":  # an Infinity Core without the API: say so, never retry forever
                    errors["base"] = "not_supported"
            if not errors:
                await self.async_set_unique_id(core_id, raise_on_progress=False)  # a pending discovery must not block it
                self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_PORT: port})
                return self.async_create_entry(title=hello.get("name") or host, data={CONF_HOST: host, CONF_PORT: port, "id": core_id})
        return self.async_show_form(step_id="user", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_HOST): str, vol.Optional(CONF_PORT, default=DEFAULT_PORT): int}))

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        props = discovery_info.properties
        if str(props.get("api", "")) != "1":
            return self.async_abort(reason="not_supported")  # an Infinity Core without the API (update it)
        core_id = str(props.get("id", ""))
        await self.async_set_unique_id(core_id)
        # one answer per interface the Core is on: keep the address it has while that is still one of them (no reload)
        addrs = {str(a) for a in discovery_info.ip_addresses}
        if any(e.unique_id == core_id and e.data.get(CONF_HOST) in addrs and e.data.get(CONF_PORT) == discovery_info.port
               for e in self._async_current_entries(include_ignore=False)):
            return self.async_abort(reason="already_configured")
        self._abort_if_unique_id_configured(updates={CONF_HOST: discovery_info.host, CONF_PORT: discovery_info.port})
        name = discovery_info.name.split("._")[0]
        self.found = {CONF_HOST: discovery_info.host, CONF_PORT: discovery_info.port, "id": core_id, "pin": str(props.get("pin", "")), "name": name}
        self.context["title_placeholders"] = {"name": name}
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title=self.found["name"], data={k: v for k, v in self.found.items() if k != "name"})
        return self.async_show_form(step_id="confirm", description_placeholders={"name": self.found["name"]})
