"""``jetkvm`` command-line entry point.

``jetkvm serve HOST`` holds the WebRTC session open; every other subcommand is a
thin client for its local control API, so they start instantly and need only the
standard library.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

from . import __version__
from .keymap import LAYOUTS

DEFAULT_PORT = 47613


def _api(port: int, method: str, path: str, payload: dict | None = None, timeout: float = 300) -> bytes:
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never proxy localhost
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            msg = json.loads(raw)["error"]
        except (ValueError, KeyError, TypeError):
            msg = raw.decode(errors="replace") or str(e)
        raise SystemExit(f"jetkvm: {msg}") from None
    except urllib.error.URLError as e:
        raise SystemExit(
            f"jetkvm: cannot reach the control API on port {port} ({e.reason}).\n"
            f"Start it first with: jetkvm serve <jetkvm-host>") from None


def _print_json(raw: bytes) -> None:
    print(json.dumps(json.loads(raw), indent=2))


def _cmd_serve(args) -> None:
    import asyncio
    import logging

    from .server import serve

    if not args.host:
        raise SystemExit("jetkvm: serve needs a host (argument or JETKVM_HOST)")
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if not args.verbose:
        for noisy in ("aioice", "aiortc", "aiohttp.access"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        asyncio.run(serve(args.host, args.port, args.password, args.layout))
    except KeyboardInterrupt:
        pass


def _cmd_shot(args) -> None:
    if args.delay:
        time.sleep(args.delay)
    query = []
    if args.crop:
        query.append(f"crop={args.crop}")
    if args.scale != 1.0:
        query.append(f"scale={args.scale}")
    png = _api(args.port, "GET", "/shot" + ("?" + "&".join(query) if query else ""))
    if args.output == "-":
        sys.stdout.buffer.write(png)
    else:
        with open(args.output, "wb") as f:
            f.write(png)
        print(os.path.abspath(args.output))


def _cmd_type(args) -> None:
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            text = f.read()
    elif args.text is None or args.text == "-":
        text = sys.stdin.read()
    else:
        text = args.text
    if args.enter:
        text += "\n"
    _print_json(_api(args.port, "POST", "/type", {"text": text, "layout": args.layout}))


def _cmd_keys(args) -> None:
    _print_json(_api(args.port, "POST", "/keys", {"combos": args.combos, "layout": args.layout}))


def _cmd_mouse(args) -> None:
    payload = {"action": args.cmd}
    if args.cmd in ("move", "click"):
        payload.update(x=args.x, y=args.y)
    if args.cmd == "click":
        payload.update(button=args.button, double=args.double)
    if args.cmd == "scroll":
        payload["amount"] = args.amount
    _print_json(_api(args.port, "POST", "/mouse", payload))


def _cmd_rpc(args) -> None:
    params = json.loads(args.params) if args.params else {}
    _print_json(_api(args.port, "POST", "/rpc", {"method": args.method, "params": params}))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jetkvm", description="Unofficial command-line client for JetKVM devices.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--port", type=int, default=int(os.environ.get("JETKVM_PORT", DEFAULT_PORT)),
                   help=f"local control API port (default {DEFAULT_PORT}, env JETKVM_PORT)")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    s = sub.add_parser("serve", help="connect to a JetKVM and run the local control API")
    s.add_argument("host", nargs="?", default=os.environ.get("JETKVM_HOST"),
                   help="JetKVM IP or hostname (env JETKVM_HOST)")
    s.add_argument("--password", default=os.environ.get("JETKVM_PASSWORD"),
                   help="device password, if one is set (env JETKVM_PASSWORD)")
    s.add_argument("--layout", choices=sorted(LAYOUTS), default="us",
                   help="default keyboard layout of the target machine (default us)")
    s.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    s.set_defaults(func=_cmd_serve)

    s = sub.add_parser("status", help="show connection, video and USB state")
    s.set_defaults(func=lambda a: _print_json(_api(a.port, "GET", "/status")))

    s = sub.add_parser("shot", help="save a PNG screenshot of the target screen")
    s.add_argument("-o", "--output", default="screen.png", help="output file, or - for stdout (default screen.png)")
    s.add_argument("--crop", metavar="X0,Y0,X1,Y1", help="crop to this box (screen pixels)")
    s.add_argument("--scale", type=float, default=1.0, help="resize factor applied after cropping")
    s.add_argument("--delay", type=float, default=0.0, help="seconds to wait before capturing")
    s.set_defaults(func=_cmd_shot)

    s = sub.add_parser("type", help="type text (from an argument, --file, or stdin)")
    s.add_argument("text", nargs="?", help="text to type; omit or use - to read stdin")
    s.add_argument("-f", "--file", help="type the contents of this file")
    s.add_argument("-e", "--enter", action="store_true", help="press Enter afterwards")
    s.add_argument("--layout", choices=sorted(LAYOUTS), help="override the keyboard layout")
    s.set_defaults(func=_cmd_type)

    s = sub.add_parser("keys", help="press key combos, e.g. ctrl+alt+f2 enter")
    s.add_argument("combos", nargs="+", help="combos such as enter, ctrl+c, ctrl+alt+delete, shift+tab")
    s.add_argument("--layout", choices=sorted(LAYOUTS), help="override the keyboard layout")
    s.set_defaults(func=_cmd_keys)

    s = sub.add_parser("move", help="move the mouse to screen pixel X Y")
    s.add_argument("x", type=float)
    s.add_argument("y", type=float)
    s.set_defaults(func=_cmd_mouse)

    s = sub.add_parser("click", help="click at screen pixel X Y")
    s.add_argument("x", type=float)
    s.add_argument("y", type=float)
    s.add_argument("--button", choices=["left", "right", "middle"], default="left")
    s.add_argument("--double", action="store_true", help="double-click")
    s.set_defaults(func=_cmd_mouse)

    s = sub.add_parser("scroll", help="scroll the wheel (positive up, negative down)")
    s.add_argument("amount", type=int)
    s.set_defaults(func=_cmd_mouse)

    s = sub.add_parser("rpc", help="send a raw JSON-RPC call to the device")
    s.add_argument("method", help="e.g. getVideoState")
    s.add_argument("params", nargs="?", help="JSON object of params")
    s.set_defaults(func=_cmd_rpc)

    s = sub.add_parser("reconnect", help="reconnect (e.g. after the web UI took the session over)")
    s.set_defaults(func=lambda a: _print_json(_api(a.port, "POST", "/reconnect")))

    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
