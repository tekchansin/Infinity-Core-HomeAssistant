"""Infinity Core API — Python SDK (draft, matches SPEC.md v1 draft 2026-11-25). MIT License.

Standard library only (asyncio + ssl): runs on any Python 3.9+, in scripts and inside Home Assistant.
Discovery uses the optional `zeroconf` package when it is installed; otherwise give the Core's address.

    import asyncio, infinity_core_api as ica

    ext = ica.Extension(id="org.example.now-playing", name="Now Playing", version="1.0.0",
                        publisher="example", uses=["read"])

    @ext.on("zone.changed")
    async def changed(params):
        np = params["zone"].get("nowPlaying")
        print(np and np["track"]["title"])

    @ext.on_ready
    async def ready(grant):
        await ext.subscribe(["zone.changed"])

    asyncio.run(ext.run(host="living-room.local"))
"""
from __future__ import annotations

import asyncio
import base64
import inspect
import logging
import hashlib
import json
import os
import random
import ssl
import struct
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("infinity_core_api")


async def _run_cb(fn, *args) -> Any:
    """A handler may be `async def` or a plain function."""
    r = fn(*args)
    return await r if inspect.isawaitable(r) else r


async def _tell(fns, *args) -> None:
    """on_state / on_ready / event handlers: one that raises is logged, never ends run()."""
    for fn in fns:
        try:
            await _run_cb(fn, *args)
        except Exception:
            log.exception("handler %s failed", getattr(fn, "__name__", fn))

__all__ = ["Extension", "Error", "PinMismatch", "Stopped", "discover", "pin_of"]

API = 1
DEFAULT_PORT = 8931
PATH = "/ext/v1"
MAX_MESSAGE = 1 << 20  # SPEC §2.2: one message ≤ 1 MB

Handler = Callable[[dict], Awaitable[Any]]


class Error(Exception):
    """A JSON-RPC error answer (SPEC §9)."""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.data = code, message, data


class PinMismatch(Exception):
    """The Core's key is not the one we know: stop and tell the person (SPEC §2.2)."""


class Stopped(Exception):
    """The Core will not work with this extension: blocked, or the Core is too old (state 'blocked' / 'upgrade')."""

    def __init__(self, state: str, reason: str = ""):
        super().__init__(f"{state}: {reason}" if reason else state)
        self.state, self.reason = state, reason


# ── TLS key pin: base64 SHA-256 of the DER SubjectPublicKeyInfo, as Core's DNS-SD `pin` ──────────────────────────
def _tlv(b: bytes, i: int) -> tuple[int, int, int]:
    """(tag, start of value, end of value) of the DER element at i."""
    tag, n = b[i], b[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(b[i:i + k], "big")
        i += k
    return tag, i, i + n


def pin_of(cert_der: bytes) -> str:
    """The pin of a certificate: Certificate → tbsCertificate → its 7th element (SPKI), skipping [0] version."""
    _, s, _ = _tlv(cert_der, 0)            # Certificate SEQUENCE
    _, s, e = _tlv(cert_der, s)            # tbsCertificate SEQUENCE
    i = s
    tag, _, end = _tlv(cert_der, i)
    if tag == 0xA0:                        # explicit version
        i = end
    for _ in range(5):                     # serial, signature, issuer, validity, subject
        _, _, i = _tlv(cert_der, i)
    _, _, end = _tlv(cert_der, i)          # subjectPublicKeyInfo
    return base64.b64encode(hashlib.sha256(cert_der[i:end]).digest()).decode()


# ── a small WebSocket client (RFC 6455): text frames, ping / pong, close ──────────────────────────────────────────
class _WebSocket:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.r, self.w = reader, writer
        self._wlock = asyncio.Lock()

    @classmethod
    async def connect(cls, host: str, port: int, path: str, ctx: ssl.SSLContext, timeout: float = 10):
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port, ssl=ctx, server_hostname=host), timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        writer.write((f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout)
        lines = head.decode("latin-1").split("\r\n")
        if " 101 " not in lines[0] + " ":
            writer.close()
            raise ConnectionError(f"no WebSocket here: {lines[0]}")
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        hdrs = {l.split(":", 1)[0].strip().lower(): l.split(":", 1)[1].strip() for l in lines[1:] if ":" in l}
        if hdrs.get("sec-websocket-accept") != accept:
            writer.close()
            raise ConnectionError("bad WebSocket handshake")
        return cls(reader, writer)

    def peer_cert(self) -> bytes:
        return self.w.get_extra_info("ssl_object").getpeercert(binary_form=True)

    async def _send(self, op: int, data: bytes) -> None:
        n = len(data)
        head = bytes([0x80 | op])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack("!Q", n)
        mask = os.urandom(4)
        body = bytes(c ^ mask[i & 3] for i, c in enumerate(data))
        async with self._wlock:
            self.w.write(head + mask + body)
            await self.w.drain()

    async def send(self, text: str) -> None:
        await self._send(0x1, text.encode())

    async def recv(self) -> Optional[str]:
        """The next text message, or None when the connection is closed."""
        parts: list[bytes] = []
        while True:
            try:
                b0, b1 = await self.r.readexactly(2)
            except (asyncio.IncompleteReadError, ConnectionError):
                return None
            fin, op, n = b0 & 0x80, b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", await self.r.readexactly(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", await self.r.readexactly(8))[0]
            if n > MAX_MESSAGE:
                await self.close()
                return None
            mask = await self.r.readexactly(4) if b1 & 0x80 else None
            data = await self.r.readexactly(n)
            if mask:
                data = bytes(c ^ mask[i & 3] for i, c in enumerate(data))
            if op == 0x9:                       # ping → pong
                await self._send(0xA, data)
            elif op == 0x8:                     # close
                await self.close()
                return None
            elif op in (0x0, 0x1):              # text (+ continuation)
                parts.append(data)
                if sum(map(len, parts)) > MAX_MESSAGE:
                    await self.close()
                    return None
                if fin:
                    return b"".join(parts).decode()

    async def close(self) -> None:
        try:
            await self._send(0x8, struct.pack("!H", 1000))
        except Exception:
            pass
        self.w.close()


# ── discovery (optional zeroconf) ────────────────────────────────────────────────────────────────────────────────
async def discover(timeout: float = 2.0) -> list[dict]:
    """Infinity Cores on the network that speak this API: [{name, host, port, id, pin}]. Needs `pip install zeroconf`."""
    try:
        from zeroconf import ServiceBrowser, Zeroconf  # type: ignore
    except ImportError as e:
        raise RuntimeError("discovery needs the zeroconf package; or give the Core's address") from e
    found: dict[str, dict] = {}

    class _L:
        def add_service(self, zc, typ, name):
            info = zc.get_service_info(typ, name, 2000)
            if not info:
                return
            txt = {k.decode(): (v or b"").decode() for k, v in info.properties.items()}
            if txt.get("api") != str(API):
                return
            addrs = info.parsed_addresses()
            found[name] = {"name": name.split("._")[0], "host": addrs[0] if addrs else info.server, "port": info.port,
                           "id": txt.get("id", ""), "pin": txt.get("pin", "")}

        def update_service(self, *a):
            pass

        def remove_service(self, *a):
            pass

    zc = Zeroconf()
    try:
        ServiceBrowser(zc, "_infinity-core._tcp.local.", _L())
        await asyncio.sleep(timeout)
    finally:
        zc.close()
    return list(found.values())


# ── the extension ────────────────────────────────────────────────────────────────────────────────────────────────
class Extension:
    """One extension. Register handlers with the decorators, then `await run(...)`. It reconnects by itself."""

    def __init__(self, *, id: str, name: str, version: str, publisher: str, uses: list[str] = (),
                 provides: list[str] = (), homepage: str = "", description: str = "", state_dir: str = ""):
        self.info = {"api": API, "id": id, "name": name, "version": version, "publisher": publisher,
                     "uses": list(uses), "provides": list(provides)}
        if homepage:
            self.info["homepage"] = homepage
        if description:
            self.info["description"] = description
        self.state_dir = state_dir or os.path.join(os.path.expanduser("~"), ".config", "infinity-core-api", id)
        self._events: dict[str, list[Handler]] = {}
        self._provided: dict[str, Handler] = {}
        self._ready: list[Callable[[dict], Awaitable[None]]] = []
        self._state_cbs: list[Callable[[str, dict], Awaitable[None]]] = []
        self._ws: Optional[_WebSocket] = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next = 0
        self._subs: set[str] = set()
        self._core_key = ""  # this session's file name for the token + pin (host_port, or the Core's id)
        self.state = "disconnected"
        self.grant: dict = {}
        self.core: dict = {}

    # decorators
    def on(self, event: str):
        def deco(fn: Handler):
            self._events.setdefault(event, []).append(fn)
            return fn
        return deco

    def provide(self, method: str):
        """Answer a call from the Core, e.g. @ext.provide("lyrics.get"). Return None for "nothing"."""
        def deco(fn: Handler):
            self._provided[method] = fn
            return fn
        return deco

    def on_ready(self, fn):
        """Called with the grant each time the extension is enabled (also after every reconnect)."""
        self._ready.append(fn)
        return fn

    def on_state(self, fn):
        """Called with (state, result) on every state change: waiting, enabled, disabled, removed, blocked, upgrade,
        and disconnected (result {}) when the connection drops; run() reconnects by itself."""
        self._state_cbs.append(fn)
        return fn

    # calls
    async def call(self, method: str, params: Optional[dict] = None, timeout: float = 30) -> Any:
        if not self._ws:
            raise ConnectionError("not connected")
        self._next += 1
        mid = self._next
        fut = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        msg = {"jsonrpc": "2.0", "id": mid, "method": method}
        if params is not None:
            msg["params"] = params
        await self._ws.send(json.dumps(msg))
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(mid, None)

    async def notify(self, method: str, params: Optional[dict] = None) -> None:
        """A notification to the Core: status.set, actions.set, volume_control.update…"""
        if self._ws:
            msg = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                msg["params"] = params
            await self._ws.send(json.dumps(msg))

    async def subscribe(self, events: list[str]) -> Any:
        """Kept across reconnects: subscribed again after every hello. Remembered only once the Core accepted it."""
        res = await self.call("events.subscribe", {"events": list(events)})
        self._subs.update(events)
        return res

    async def unsubscribe(self, events: list[str]) -> Any:
        self._subs.difference_update(events)
        return await self.call("events.unsubscribe", {"events": list(events)})

    # state on disk: the token and the pin we trust, per Core
    def _load(self, core_key: str) -> dict:
        try:
            with open(os.path.join(self.state_dir, f"{core_key}.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self, core_key: str, data: dict) -> None:
        os.makedirs(self.state_dir, exist_ok=True)
        p = os.path.join(self.state_dir, f"{core_key}.json")
        with open(p + ".tmp", "w") as f:
            json.dump(data, f)
        os.chmod(p + ".tmp", 0o600)
        os.replace(p + ".tmp", p)

    async def run(self, host: str = "", port: int = DEFAULT_PORT, pin: str = "", core_id: str = "") -> None:
        """Connect and keep connected. Without `host` the first Core found by discovery is used."""
        delay = 1
        while True:
            try:
                if not host:
                    cores = await discover()
                    if not cores:
                        raise ConnectionError("no Infinity Core found")
                    c = cores[0]
                    host, port, pin, core_id = c["host"], c["port"], c["pin"], c["id"]
                await self._session(host, port, pin, core_id or f"{host}_{port}")
                # turned off (or Not now): try again later, quietly (SPEC §3.2) — not every second
                delay = 60 if self.state == "disabled" else 1
            except (PinMismatch, Stopped):
                raise
            except (OSError, ConnectionError, asyncio.TimeoutError, asyncio.IncompleteReadError) as e:
                # a lost or refused connection is routine; anything else (an unwritable state_dir, a bad host name…) is said
                if not isinstance(e, (ConnectionError, asyncio.TimeoutError, asyncio.IncompleteReadError)):
                    log.warning("connecting to %s: %s — trying again", host or "Infinity Core", e)
            self._ws = None
            if self.state != "disconnected":  # tell on_state once, so a UI can show the Core as gone
                self.state = "disconnected"
                await _tell(self._state_cbs, "disconnected", {})
            await asyncio.sleep(delay + random.random())
            delay = min(delay * 2, 60)

    async def _session(self, host: str, port: int, pin: str, core_key: str) -> None:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # not create_default_context(): it reads the CA files (blocks the loop)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # self-signed: the pin is the check
        ws = await _WebSocket.connect(host, port, PATH, ctx)
        self._core_key = core_key
        saved = await asyncio.to_thread(self._load, core_key)  # off the loop: Home Assistant warns on blocking file reads
        seen = pin_of(ws.peer_cert())
        trusted = pin or saved.get("pin")
        if trusted and seen != trusted:
            await ws.close()
            raise PinMismatch(f"{host}: the Core's key changed. Remove the extension's saved state if this is expected.")
        if not trusted:
            saved["pin"] = seen
            await asyncio.to_thread(self._save, core_key, saved)
        self._ws = ws
        reader = asyncio.ensure_future(self._read(ws))
        try:
            hello = dict(self.info)
            if saved.get("token"):
                hello["token"] = saved["token"]
            res = await self.call("hello", hello)
            await self._apply_state(res, core_key)
            await reader
        finally:
            reader.cancel()
            ws.w.close()  # also when run() is cancelled (e.g. Home Assistant unloads): never leave the socket open
            self._ws = None
            for f in self._pending.values():
                if not f.done():
                    f.set_exception(ConnectionError("disconnected"))
            self._pending.clear()

    async def _apply_state(self, res: dict, core_key: str) -> None:
        state = res.get("state", "")
        if res.get("token") or state == "removed":
            saved = await asyncio.to_thread(self._load, core_key)
            if res.get("token"):
                saved["token"] = res["token"]
            else:
                saved.pop("token", None)
            await asyncio.to_thread(self._save, core_key, saved)
        if "core" in res:
            self.core = res["core"]
        if "grant" in res:
            self.grant = res["grant"]
        self.state = state
        await _tell(self._state_cbs, state, res)
        if state in ("blocked", "upgrade"):
            raise Stopped(state, res.get("reason", ""))
        if state == "enabled":
            if self._subs:
                try:  # e.g. -32003 once the Owner unticked `read`: logged, the extension carries on
                    await self.call("events.subscribe", {"events": sorted(self._subs)})
                except Error as e:
                    log.warning("subscribing again to %s refused: %s", sorted(self._subs), e)
            await _tell(self._ready, self.grant)

    async def _read(self, ws: _WebSocket) -> None:
        while True:
            text = await ws.recv()
            if text is None:  # closed: what waits for an answer gets none (hello too — not after 30 s)
                for f in self._pending.values():
                    if not f.done():
                        f.set_exception(ConnectionError("disconnected"))
                return
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            if "method" in msg:
                asyncio.ensure_future(self._incoming(msg))
            elif "id" in msg and msg["id"] in self._pending:
                fut = self._pending[msg["id"]]
                if fut.done():
                    continue
                if "error" in msg:
                    e = msg["error"]
                    fut.set_exception(Error(e.get("code", 0), e.get("message", ""), e.get("data")))
                else:
                    fut.set_result(msg.get("result"))

    async def _incoming(self, msg: dict) -> None:
        method, params, mid = msg["method"], msg.get("params") or {}, msg.get("id")
        if mid is None:  # a notification: state change or an event
            if method == "extension.state":
                try:
                    await self._apply_state(params, self._core_key)
                except Stopped:
                    pass  # blocked while connected: run() sees the socket close; the next hello says so again
            await _tell(self._events.get(method, []), params)
            return
        fn = self._provided.get(method)
        if not fn:
            reply = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"not provided: {method}"}}
        else:
            try:
                reply = {"jsonrpc": "2.0", "id": mid, "result": await _run_cb(fn, params)}
            except Error as e:
                reply = {"jsonrpc": "2.0", "id": mid, "error": {"code": e.code, "message": e.message}}
            except Exception as e:  # never let one bad answer drop the connection
                reply = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)[:200]}}
        if self._ws:
            await self._ws.send(json.dumps(reply))
