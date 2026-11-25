"""Local HTTP control API wrapping a JetKVMSession.

Binds to 127.0.0.1 only and has no authentication: anything that can reach
the port on this machine can drive the KVM.
"""

from __future__ import annotations

import logging

from aiohttp import web

from .session import JetKVMSession, NotConnected

log = logging.getLogger("jetkvm")


def make_app(session: JetKVMSession, default_layout: str = "us") -> web.Application:
    routes = web.RouteTableDef()

    async def body(request: web.Request) -> dict:
        if not request.can_read_body:
            return {}
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        return data

    @routes.get("/status")
    async def status(_request):
        return web.json_response(session.status())

    @routes.get("/shot")
    async def shot(request):
        crop = request.query.get("crop")
        box = tuple(int(v) for v in crop.split(",")) if crop else None
        if box is not None and len(box) != 4:
            raise ValueError("crop must be x0,y0,x1,y1")
        png = await session.screenshot_png(crop=box, scale=float(request.query.get("scale", 1.0)))
        return web.Response(body=png, content_type="image/png")

    @routes.post("/type")
    async def type_(request):
        b = await body(request)
        n = await session.type_text(b["text"], b.get("layout") or default_layout)
        return web.json_response({"typed": n})

    @routes.post("/keys")
    async def keys(request):
        b = await body(request)
        await session.press(list(b["combos"]), b.get("layout") or default_layout)
        return web.json_response({"pressed": b["combos"]})

    @routes.post("/mouse")
    async def mouse(request):
        b = await body(request)
        action = b.get("action")
        if action == "move":
            await session.mouse_move(b["x"], b["y"])
        elif action == "click":
            await session.click(b["x"], b["y"], b.get("button", "left"), bool(b.get("double")))
        elif action == "scroll":
            await session.scroll(b["amount"])
        else:
            raise ValueError(f"unknown mouse action {action!r}")
        return web.json_response({"ok": True})

    @routes.post("/rpc")
    async def rpc(request):
        b = await body(request)
        return web.json_response(await session.call(b["method"], b.get("params") or {}))

    @routes.post("/reconnect")
    async def reconnect(_request):
        session.request_reconnect()
        return web.json_response({"ok": True})

    @web.middleware
    async def errors(request, handler):
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except NotConnected as e:
            return web.json_response({"error": str(e)}, status=503)
        except (ValueError, KeyError, TypeError) as e:
            msg = f"missing field {e}" if isinstance(e, KeyError) else str(e)
            return web.json_response({"error": msg}, status=400)
        except Exception as e:  # noqa: BLE001
            log.exception("request failed")
            return web.json_response({"error": str(e) or repr(e)}, status=500)

    app = web.Application(middlewares=[errors])
    app.add_routes(routes)
    return app


async def serve(host: str, port: int, password: str | None = None, layout: str = "us") -> None:
    session = JetKVMSession(host, password)
    runner = web.AppRunner(make_app(session, layout), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    log.info("control API listening on http://127.0.0.1:%d", port)
    try:
        await session.run_forever()
    finally:
        await session.close()
        await runner.cleanup()
