"""Each Infinity Core zone (a profile's music) as a media player."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import aiohttp
from homeassistant.components.media_player import (
    MediaPlayerEntity, MediaPlayerEntityFeature as F, MediaPlayerState, RepeatMode)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

STATES = {"playing": MediaPlayerState.PLAYING, "paused": MediaPlayerState.PAUSED, "loading": MediaPlayerState.BUFFERING,
          "stopped": MediaPlayerState.IDLE}
REPEAT = {"off": RepeatMode.OFF, "all": RepeatMode.ALL, "one": RepeatMode.ONE}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    hub = entry.runtime_data
    made: set[str] = set()

    @callback
    def new_zone(zid: str) -> None:
        if zid not in made:
            made.add(zid)
            add([ZonePlayer(hub, entry, zid)])

    hub.on_new_zone = new_zone
    for zid in list(hub.zones):
        new_zone(zid)


class ZonePlayer(MediaPlayerEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_supported_features = (F.PLAY | F.PAUSE | F.STOP | F.NEXT_TRACK | F.PREVIOUS_TRACK | F.VOLUME_SET | F.VOLUME_MUTE
                                | F.VOLUME_STEP | F.SEEK | F.SHUFFLE_SET | F.REPEAT_SET | F.SELECT_SOURCE)

    def __init__(self, hub, entry: ConfigEntry, zid: str) -> None:
        self.hub, self.zid = hub, zid
        core = entry.data.get("id") or entry.data["host"]
        self._attr_unique_id = f"{core}_{zid}"
        self._attr_device_info = {"identifiers": {(DOMAIN, core)}, "name": entry.title, "manufacturer": "Infinity Core"}

    @property
    def supported_features(self) -> F:
        if self.volume_level is None:  # a fixed volume (line out) or none: the Core refuses volume calls (-32010)
            return self._attr_supported_features & ~(F.VOLUME_SET | F.VOLUME_MUTE | F.VOLUME_STEP)
        return self._attr_supported_features

    @property
    def z(self) -> dict:
        return self.hub.zones.get(self.zid, {})

    @property
    def name(self) -> str:
        return self.z.get("name") or self.zid

    @property
    def available(self) -> bool:
        return self.hub.ext.state == "enabled" and bool(self.z)

    async def async_added_to_hass(self) -> None:
        self.hub.listeners.append(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        self.hub.listeners.remove(self.async_write_ha_state)

    @property
    def state(self):
        return STATES.get(self.z.get("state", "stopped"), MediaPlayerState.IDLE)

    def _track(self) -> dict:
        # a private session (SPEC §10): play / pause stay, but no title, artist, album or cover — HA's recorder keeps
        # every attribute it sees
        if self.z.get("private"):
            return {}
        return (self.z.get("nowPlaying") or {}).get("track") or {}

    @property
    def media_title(self):
        return self._track().get("title")

    @property
    def media_artist(self):
        return ", ".join(self._track().get("artists", [])) or None

    @property
    def media_album_name(self):
        return (self._track().get("album") or {}).get("title") or None

    @property
    def media_duration(self):
        return self._track().get("duration")

    @property
    def media_position(self):
        return None if self.z.get("private") else (self.z.get("nowPlaying") or {}).get("position")

    @property
    def media_position_updated_at(self):
        at = None if self.z.get("private") else (self.z.get("nowPlaying") or {}).get("at")
        return datetime.fromtimestamp(at / 1000, timezone.utc) if at else None

    @property
    def media_image_hash(self):
        img = self._track().get("image")
        return hashlib.sha256(img.encode()).hexdigest()[:16] if img else None  # the image id; its address changes

    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        """Through Home Assistant's proxy: HA's own fetch checks the certificate, and the Core's is self-signed."""
        img = self._track().get("image")
        if not img:
            return None, None
        try:  # a fresh signed address each time (valid 10 minutes)
            url = (await self.hub.call("image.url", {"image": img, "size": 600}))["url"]
            session = async_get_clientsession(self.hass, verify_ssl=False)
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    return None, None
                return await r.read(), r.headers.get("Content-Type", "image/jpeg").split(";")[0]
        except (HomeAssistantError, aiohttp.ClientError, TimeoutError, KeyError, TypeError):
            return None, None  # no cover is not an error

    def _vol(self) -> dict:
        return (self.z.get("output") or {}).get("volume") or {}

    @property
    def volume_level(self):
        v = self._vol()
        if not v or v.get("fixed"):
            return None
        span = (v.get("max", 100) - v.get("min", 0)) or 100
        return (v.get("value", 0) - v.get("min", 0)) / span

    @property
    def is_volume_muted(self):
        return self._vol().get("muted")

    @property
    def shuffle(self):
        return self.z.get("shuffle")

    @property
    def repeat(self):
        return REPEAT.get(self.z.get("repeat", "off"))

    @property
    def source(self):
        return (self.z.get("output") or {}).get("name")

    @property
    def source_list(self):
        return [o["name"] for o in self.hub.outputs]

    async def _t(self, action: str) -> None:
        await self.hub.call("transport.control", {"zone": self.zid, "action": action})

    async def async_media_play(self) -> None:
        await self._t("play")

    async def async_media_pause(self) -> None:
        await self._t("pause")

    async def async_media_stop(self) -> None:
        await self._t("stop")

    async def async_media_next_track(self) -> None:
        await self._t("next")

    async def async_media_previous_track(self) -> None:
        await self._t("previous")

    async def async_media_seek(self, position: float) -> None:
        await self.hub.call("transport.seek", {"zone": self.zid, "position": position})

    async def async_set_volume_level(self, volume: float) -> None:
        v = self._vol()
        lo, hi = v.get("min", 0), v.get("max", 100)
        # rounded: 0.57 × 100 = 56.999…, and the Core truncates
        await self.hub.call("volume.set", {"zone": self.zid, "value": round(lo + volume * (hi - lo), 3)})

    async def async_mute_volume(self, mute: bool) -> None:
        await self.hub.call("volume.mute", {"zone": self.zid, "mute": mute})

    async def async_set_shuffle(self, shuffle: bool) -> None:
        await self.hub.call("zone.shuffle", {"zone": self.zid, "on": shuffle})

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        await self.hub.call("zone.repeat", {"zone": self.zid, "mode": {RepeatMode.ALL: "all", RepeatMode.ONE: "one"}.get(repeat, "off")})

    async def async_select_source(self, source: str) -> None:
        out = next((o for o in self.hub.outputs if o["name"] == source), None)
        if out:
            await self.hub.call("zone.transfer", {"zone": self.zid, "to": out["id"]})
