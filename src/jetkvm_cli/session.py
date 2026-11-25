"""A WebRTC session with a JetKVM device.

Mirrors what the device's own web UI does:

1. Open the signaling WebSocket at ``/webrtc/signaling/client`` (the device
   rejects it with 403 unless an ``Origin`` header matching the device is sent).
2. Send an SDP offer (base64-encoded JSON) with recv-only video/audio and an
   ``rpc`` data channel; apply the answer and any trickled ICE candidates.
3. Receive the H.264 screen stream and keep the latest decoded frame.
4. Drive the keyboard and mouse with JSON-RPC 2.0 calls on the ``rpc``
   channel (``keyboardReport``, ``absMouseReport``, ``wheelReport``).
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import time
from typing import Any

import aiohttp
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.sdp import candidate_from_sdp

from .keymap import parse_combo, text_to_strokes

log = logging.getLogger("jetkvm")

MOUSE_BUTTONS = {"left": 1, "right": 2, "middle": 4}
ABS_MAX = 32767


class NotConnected(ConnectionError):
    pass


class JetKVMSession:
    def __init__(self, host: str, password: str | None = None):
        self.host = host
        self.password = password
        self.origin = f"http://{host}"

        self.frame = None
        self.frame_time = 0.0
        self.frame_count = 0
        self.connection_state = "new"
        self.device_version: str | None = None
        self.events: dict[str, Any] = {}
        self.taken_over = False

        self._ready = asyncio.Event()
        self._have_frame = asyncio.Event()
        self._reconnect = asyncio.Event()
        self._hid_lock = asyncio.Lock()
        self._closing = False
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._rpc = None
        self._pc: RTCPeerConnection | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._http: aiohttp.ClientSession | None = None
        self._tasks: list[asyncio.Task] = []
        self._was_connected = False

    # ------------------------------------------------------------ lifecycle
    async def run_forever(self) -> None:
        """Connect, and keep reconnecting with backoff until ``close()``."""
        backoff = 1.0
        while not self._closing:
            self._was_connected = False
            try:
                await self._connect_once()
            except Exception as e:  # noqa: BLE001 - any failure means "retry"
                log.warning("connection error: %s", e)
            finally:
                await self._teardown()
            if self._closing:
                break
            if self._was_connected:
                backoff = 1.0
            if self.taken_over:
                log.warning("another client took over the KVM; run `jetkvm reconnect` to take it back")
                self._reconnect.clear()
                await self._reconnect.wait()
                self.taken_over = False
                backoff = 1.0
                continue
            log.info("reconnecting in %.0fs", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    def request_reconnect(self) -> None:
        self.taken_over = False
        self._reconnect.set()
        if self._ws is not None and not self._ws.closed:
            asyncio.ensure_future(self._ws.close())

    async def close(self) -> None:
        self._closing = True
        self._reconnect.set()
        await self._teardown()

    async def _connect_once(self) -> None:
        self._http = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        if self.password:
            await self._login()
        self._ws = ws = await self._http.ws_connect(
            f"ws://{self.host}/webrtc/signaling/client", headers={"Origin": self.origin})
        log.info("signaling connected to %s", self.host)

        # LAN only: no STUN/TURN (aiortc would otherwise default to a public STUN server).
        self._pc = pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        pc.addTransceiver("video", direction="recvonly")
        pc.addTransceiver("audio", direction="recvonly")
        channel = pc.createDataChannel("rpc")

        @channel.on("open")
        def _on_open():
            log.info("rpc channel open")
            self._rpc = channel
            self._was_connected = True
            self._ready.set()

        @channel.on("close")
        def _on_close():
            self._ready.clear()

        @channel.on("message")
        def _on_message(data):
            self._handle_rpc_message(data)

        @pc.on("track")
        def _on_track(track):
            if track.kind == "video":
                self._tasks.append(asyncio.ensure_future(self._consume_video(track)))

        @pc.on("connectionstatechange")
        async def _on_state():
            self.connection_state = pc.connectionState
            log.info("connection state: %s", pc.connectionState)
            if pc.connectionState in ("failed", "closed") and not ws.closed:
                await ws.close()

        offer_sent = False
        remote_set = False
        queued: list[dict] = []

        async def send_offer():
            nonlocal offer_sent
            if offer_sent:
                return
            offer_sent = True
            await pc.setLocalDescription(await pc.createOffer())
            ld = pc.localDescription
            sd = base64.b64encode(json.dumps({"type": ld.type, "sdp": ld.sdp}).encode()).decode()
            await ws.send_str(json.dumps({"type": "offer", "data": {"sd": sd}}))

        async def offer_fallback():
            # The UI offers after "device-metadata"; don't hang if it never comes.
            await asyncio.sleep(3)
            try:
                await send_offer()
            except Exception as e:  # noqa: BLE001
                log.warning("sending offer failed: %s", e)
                await ws.close()

        async def keepalive():
            try:
                while not ws.closed:
                    await asyncio.sleep(20)
                    await ws.send_str("ping")
            except (ConnectionError, RuntimeError):
                pass

        async def add_candidate(d: dict):
            s = d.get("candidate") or ""
            if not s:
                return
            cand = candidate_from_sdp(s.split(":", 1)[1] if s.startswith("candidate:") else s)
            cand.sdpMid = d.get("sdpMid")
            cand.sdpMLineIndex = d.get("sdpMLineIndex")
            await pc.addIceCandidate(cand)

        self._tasks += [asyncio.ensure_future(offer_fallback()), asyncio.ensure_future(keepalive())]

        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            if msg.data == "pong":
                continue
            try:
                m = json.loads(msg.data)
            except ValueError:
                continue
            kind = m.get("type")
            if kind == "device-metadata":
                self.device_version = (m.get("data") or {}).get("deviceVersion")
                log.info("device firmware %s", self.device_version)
                await send_offer()
            elif kind == "answer":
                answer = json.loads(base64.b64decode(m["data"]))
                await pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
                remote_set = True
                for d in queued:
                    await add_candidate(d)
                queued.clear()
            elif kind == "new-ice-candidate":
                if remote_set:
                    await add_candidate(m["data"])
                else:
                    queued.append(m["data"])
            elif kind == "other-session-connected":
                self.taken_over = True
                await ws.close()
        log.info("signaling closed (code %s)", ws.close_code)

    async def _login(self) -> None:
        assert self._http is not None
        async with self._http.post(f"{self.origin}/auth/login-local", json={"password": self.password},
                                   headers={"Origin": self.origin}) as r:
            if r.status != 200:
                raise NotConnected(f"login failed (HTTP {r.status})")

    async def _teardown(self) -> None:
        self._ready.clear()
        self._rpc = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(NotConnected("KVM connection lost"))
        self._pending.clear()
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        if self._pc is not None:
            await self._pc.close()
            self._pc = None
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        if self._http is not None:
            await self._http.close()
            self._http = None
        self.connection_state = "taken-over" if self.taken_over else "disconnected"

    async def _consume_video(self, track) -> None:
        while True:
            try:
                frame = await track.recv()
            except Exception:  # noqa: BLE001 - track ended
                return
            self.frame = frame
            self.frame_time = time.time()
            self.frame_count += 1
            self._have_frame.set()

    def _handle_rpc_message(self, data) -> None:
        try:
            m = json.loads(data)
        except (TypeError, ValueError):
            return
        if "method" in m:  # device-pushed event, e.g. videoInputState, usbState
            self.events[m["method"]] = m.get("params")
            return
        fut = self._pending.get(m.get("id"))
        if fut is not None and not fut.done():
            fut.set_result(m)

    # ------------------------------------------------------------ RPC
    async def wait_ready(self, timeout: float = 20.0) -> None:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except asyncio.TimeoutError:
            state = "taken over by another client" if self.taken_over else self.connection_state
            raise NotConnected(f"not connected to the KVM ({state})") from None

    async def call(self, method: str, params: dict | None = None, timeout: float = 5.0) -> dict:
        """Send a JSON-RPC request on the rpc channel and return the raw response."""
        await self.wait_ready()
        self._next_id += 1
        req_id = self._next_id
        fut = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        try:
            self._rpc.send(json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}, "id": req_id}))
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(req_id, None)

    async def _call_ok(self, method: str, params: dict) -> Any:
        r = await self.call(method, params)
        if "error" in r:
            err = r["error"]
            raise RuntimeError(f"{method} failed: {err.get('message')} {err.get('data') or ''}".strip())
        return r.get("result")

    # ------------------------------------------------------------ keyboard
    async def _key_report(self, keys: list[int], modifier: int) -> None:
        # The device requires exactly six key slots.
        await self._call_ok("keyboardReport", {"keys": (list(keys) + [0] * 6)[:6], "modifier": modifier})

    async def _tap(self, code: int | None, modifier: int) -> None:
        if modifier:
            await self._key_report([], modifier)
        if code is not None:
            await self._key_report([code], modifier)
            await self._key_report([], modifier)
        if modifier:
            await self._key_report([], 0)

    async def type_text(self, text: str, layout: str = "us") -> int:
        strokes = text_to_strokes(text, layout)  # validate everything before typing anything
        async with self._hid_lock:
            for code, mod in strokes:
                await self._tap(code, mod)
        return len(strokes)

    async def press(self, combos: list[str], layout: str = "us") -> None:
        parsed = [parse_combo(c, layout) for c in combos]
        async with self._hid_lock:
            for code, mod in parsed:
                await self._tap(code, mod)

    # ------------------------------------------------------------ mouse
    def screen_size(self) -> tuple[int, int]:
        if self.frame is not None:
            return self.frame.width, self.frame.height
        video = self.events.get("videoInputState") or {}
        if video.get("width") and video.get("height"):
            return video["width"], video["height"]
        raise NotConnected("screen size unknown (no video yet)")

    def _to_abs(self, x: float, y: float) -> tuple[int, int]:
        w, h = self.screen_size()
        return (round(min(max(x / w, 0.0), 1.0) * ABS_MAX), round(min(max(y / h, 0.0), 1.0) * ABS_MAX))

    async def _mouse(self, x: float, y: float, buttons: int) -> None:
        ax, ay = self._to_abs(x, y)
        await self._call_ok("absMouseReport", {"x": ax, "y": ay, "buttons": buttons})

    async def mouse_move(self, x: float, y: float) -> None:
        async with self._hid_lock:
            await self._mouse(x, y, 0)

    async def click(self, x: float, y: float, button: str = "left", double: bool = False) -> None:
        if button not in MOUSE_BUTTONS:
            raise ValueError(f"unknown mouse button {button!r}")
        b = MOUSE_BUTTONS[button]
        async with self._hid_lock:
            await self._mouse(x, y, 0)
            for _ in range(2 if double else 1):
                await self._mouse(x, y, b)
                await self._mouse(x, y, 0)

    async def scroll(self, amount: int) -> None:
        """Scroll the wheel: positive is up, negative is down."""
        async with self._hid_lock:
            await self._call_ok("wheelReport", {"wheelY": max(-127, min(127, int(amount))), "wheelX": 0})

    # ------------------------------------------------------------ video
    async def screenshot_png(self, crop: tuple[int, int, int, int] | None = None, scale: float = 1.0,
                             timeout: float = 20.0) -> bytes:
        try:
            await asyncio.wait_for(self._have_frame.wait(), timeout)
        except asyncio.TimeoutError:
            raise NotConnected("no video frame received yet") from None
        img = self.frame.to_image()
        if crop:
            img = img.crop(crop)
        if scale != 1.0:
            img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def status(self) -> dict:
        try:
            size = list(self.screen_size())
        except NotConnected:
            size = None
        return {
            "host": self.host,
            "connection": self.connection_state,
            "ready": self._ready.is_set(),
            "taken_over": self.taken_over,
            "firmware": self.device_version,
            "screen": size,
            "frames": self.frame_count,
            "frame_age_s": round(time.time() - self.frame_time, 1) if self.frame is not None else None,
            "video": self.events.get("videoInputState"),
            "usb": self.events.get("usbState"),
            "keyboard_leds": self.events.get("keyboardLedState"),
        }
