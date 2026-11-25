# jetkvm-cli

An unofficial command-line client for [JetKVM](https://jetkvm.com) devices. Take screenshots of the
attached machine's screen, type text, press key combos and drive the mouse from a terminal or a script,
with no browser involved.

It was built to rescue a headless server whose network was misconfigured: the only way in was the
console, and this let a script (or an AI coding agent) read the screen and type the fix.

> Not affiliated with or endorsed by JetKVM. It speaks the same WebRTC/JSON-RPC protocol as the
> device's own web UI, which is not a documented public API and may change between firmware releases.

## Install

Requires Python 3.10+.

```sh
pip install git+https://github.com/jkri-ch/jetkvm-cli
```

## Quick start

```sh
# 1. Hold a session open to the KVM (leave this running)
jetkvm serve 192.168.1.50

# 2. In another terminal
jetkvm status                         # connection, resolution, USB state
jetkvm shot -o screen.png             # screenshot of the target machine
jetkvm type "ip -br addr" --enter     # type a command and press Enter
jetkvm shot --delay 1 -o after.png    # see what it did
```

`serve` keeps one WebRTC session open and exposes a small HTTP API on `127.0.0.1:47613`. All other
commands are thin clients for that API, so they return instantly and you can call them from any
language.

## Commands

| Command | What it does |
|---|---|
| `jetkvm serve HOST [--password PW] [--layout us\|uk]` | Connect to the KVM and run the local control API. Reconnects automatically. |
| `jetkvm status` | Connection state, firmware, screen size, video/USB state, keyboard LEDs. |
| `jetkvm shot [-o FILE\|-] [--crop X0,Y0,X1,Y1] [--scale N] [--delay S]` | Save a PNG of the current screen. `--crop` uses screen pixels. |
| `jetkvm type TEXT [--enter]` / `-f FILE` / stdin | Type text. `\n` presses Enter. |
| `jetkvm keys COMBO...` | Press combos in order, e.g. `jetkvm keys ctrl+alt+f2 enter`. |
| `jetkvm move X Y` / `jetkvm click X Y [--button right] [--double]` | Absolute mouse, in screen pixels. |
| `jetkvm scroll N` | Scroll the wheel (positive up, negative down). |
| `jetkvm rpc METHOD [JSON]` | Send a raw JSON-RPC call, e.g. `jetkvm rpc getVideoState`. |
| `jetkvm reconnect` | Take the session back after the web UI took it over. |

Combo names are case-insensitive: `enter`, `esc`, `tab`, `backspace`, `delete`, `f1`–`f12`, arrows
(`up`, `down`, `left`, `right`), `home`, `end`, `pageup`, `pagedown`, `printscreen`, plus modifiers
`ctrl`, `shift`, `alt`, `meta`/`win`, `altgr`. Use `plus` for the `+` key.

Environment variables: `JETKVM_HOST`, `JETKVM_PASSWORD`, `JETKVM_PORT`.

### Keyboard layouts

A USB keyboard sends key positions, not characters, so the layout must match the one configured on the
target machine. `us` (default) and `uk` are built in. Text that can't be typed with the chosen layout is
rejected before any key is pressed. To check, type a test string without pressing Enter and take a
screenshot:

```sh
jetkvm type '# test "@|\~'; jetkvm shot -o check.png; jetkvm keys ctrl+u
```

## HTTP API

If you'd rather not shell out, talk to `serve` directly:

| Method | Path | Body / query |
|---|---|---|
| GET | `/status` | |
| GET | `/shot` | `?crop=x0,y0,x1,y1&scale=2`, returns `image/png` |
| POST | `/type` | `{"text": "...", "layout": "us"}` |
| POST | `/keys` | `{"combos": ["ctrl+c", "enter"]}` |
| POST | `/mouse` | `{"action": "move"\|"click"\|"scroll", "x", "y", "button", "double", "amount"}` |
| POST | `/rpc` | `{"method": "...", "params": {}}` |
| POST | `/reconnect` | |

Errors come back as `{"error": "..."}` with status 400 (bad input), 503 (not connected) or 500.

## How it works

1. Opens the device's signaling WebSocket at `ws://HOST/webrtc/signaling/client`. The device returns
   403 unless the `Origin` header matches it, the same as a browser on its own page.
2. Sends a WebRTC offer (base64-encoded JSON SDP) with receive-only video and audio and an `rpc` data
   channel, then applies the answer and trickled ICE candidates. There's no STUN or TURN, so it works on
   the LAN only.
3. Decodes the H.264 screen stream with [aiortc](https://github.com/aiortc/aiortc)/PyAV and keeps the
   latest frame for `/shot`.
4. Sends HID input as JSON-RPC 2.0 on the `rpc` channel:
   - `keyboardReport`: `{"keys": [six HID usage codes], "modifier": bitmask}`.
   - `absMouseReport`: `{"x", "y"}` scaled to 0–32767, plus `buttons`.
   - `wheelReport`: `{"wheelY", "wheelX"}`.

## Caveats

- **One session at a time.** Connecting takes over from any open browser session, and opening the web
  UI takes over from `serve`. After that, `serve` waits until you run `jetkvm reconnect`.
- **The control API has no authentication.** It only listens on `127.0.0.1`, but anything running on
  your machine can use it to drive the KVM while `serve` is up.
- **Password login is untested.** `--password` follows what the web UI does (`POST /auth/login-local`),
  but it has only been tested against a device in no-password mode. Setting a device password is still
  strongly recommended.
- **Tested hardware:** a JetKVM Mini (Ethernet) on firmware 0.5.20, from Windows 11 with Python 3.13.

## Development

```sh
git clone https://github.com/jkri-ch/jetkvm-cli && cd jetkvm-cli
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

## License

MIT
