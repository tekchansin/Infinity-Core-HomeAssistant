"""Infinity Core: each zone as a media player. It connects as an extension; enable it in Infinity Core › Settings › Extensions.

The SDK (infinity_core_api.py, MIT) is copied beside this file, so the integration needs nothing from PyPI.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from . import infinity_core_api as ica
from .const import DOMAIN, EXT_ID

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.MEDIA_PLAYER]


class Hub:
    """One Infinity Core: the connection, its zones and outputs, and who listens."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass, self.entry = hass, entry
        self.ext = ica.Extension(id=EXT_ID, name="Home Assistant", version="1.0.0", publisher="Infinity Core",
                                 uses=["read", "control"], description="Each zone as a media player in Home Assistant.",
                                 state_dir=hass.config.path(".storage", f"infinity_core_{entry.entry_id}"))
        self.zones: dict[str, dict] = {}
        self.outputs: list[dict] = []
        self.listeners: list[Callable[[], None]] = []
        self.on_new_zone: Callable[[str], None] | None = None
        self.ext.on_state(self._state)
        self.ext.on_ready(self._ready)
        self.ext.on("zone.changed")(self._zone)  # on() is a decorator factory
        self.ext.on("outputs.changed")(self._outputs)
        self.ext.on("zone.removed")(self._zone_removed)

    async def _state(self, state: str, res: dict) -> None:
        nid = f"{DOMAIN}_{self.entry.entry_id}_enable"
        if state == "waiting":
            persistent_notification.async_create(
                self.hass, "Open Infinity Core › Settings › Extensions and enable **Home Assistant** (Review › Enable). "
                "Choose “Everyone” to see every profile's zones.", "Infinity Core: enable Home Assistant", nid)
        elif state != "disconnected":  # a dropped link says nothing about Enable
            persistent_notification.async_dismiss(self.hass, nid)
        self._changed()  # entities re-read `available` (disabled, removed, Core gone)

    async def _ready(self, grant: dict) -> None:
        r = await self.ext.subscribe(["zone.changed", "zone.removed", "outputs.changed"])
        self.zones = {}  # the grant may have changed (Everyone ↔ one profile): only the zones it shows now
        for z in r.get("zones", []):
            await self._zone({"zone": z})
        self.outputs = (await self.ext.call("outputs.list")).get("outputs", [])
        self._changed()

    async def _zone(self, p: dict) -> None:
        z = p["zone"]
        new = z["id"] not in self.zones
        self.zones[z["id"]] = z
        if new and self.on_new_zone:
            self.on_new_zone(z["id"])
        self._changed()

    async def _zone_removed(self, p: dict) -> None:
        """A profile was deleted: its entity goes unavailable (kept, so automations still name it)."""
        self.zones.pop(p.get("zone") or p.get("id"), None)  # the Core sends {zone}; SPEC §6 says {id}
        self._changed()

    async def _outputs(self, p: dict) -> None:
        self.outputs = p.get("outputs", [])
        self._changed()

    def _changed(self) -> None:
        for cb in list(self.listeners):
            cb()

    async def call(self, method: str, params: dict[str, Any]) -> Any:
        try:
            return await self.ext.call(method, params)
        except (ica.Error, ConnectionError, asyncio.TimeoutError) as e:  # a clear message in HA, not a traceback
            # outside the grant / output in use / fixed volume: nothing is broken (REST 400, no error log)
            cls = ServiceValidationError if getattr(e, "code", 0) in (-32003, -32009, -32010) else HomeAssistantError
            raise cls(f"Infinity Core: {getattr(e, 'message', '') or e or 'no answer'}") from e


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hub = Hub(hass, entry)
    entry.runtime_data = hub
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_create_background_task(hass, _run(hub), f"{DOMAIN} {entry.data[CONF_HOST]}")
    return True


async def _run(hub: Hub) -> None:
    data = hub.entry.data
    try:
        await hub.ext.run(host=data[CONF_HOST], port=data.get(CONF_PORT, 8931), pin=data.get("pin", ""), core_id=data.get("id", ""))
    except ica.PinMismatch as e:
        _LOGGER.error("Not connecting: %s", e)
        persistent_notification.async_create(hub.hass, "Infinity Core's key changed. Remove this integration and add it again if "
                                             "you reinstalled Infinity Core.", "Infinity Core: key changed", f"{DOMAIN}_{hub.entry.entry_id}_pin")
    except ica.Stopped as e:
        _LOGGER.warning("Infinity Core will not work with Home Assistant: %s", e)
    except asyncio.CancelledError:
        raise


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The extension's token and the Core's pin for this entry go with it."""
    import shutil
    await hass.async_add_executor_job(shutil.rmtree, hass.config.path(".storage", f"infinity_core_{entry.entry_id}"), True)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    for n in ("enable", "pin"):  # this entry's notifications go with it; the next setup raises them again if still true
        persistent_notification.async_dismiss(hass, f"{DOMAIN}_{entry.entry_id}_{n}")
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
