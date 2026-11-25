"""Auth handling against a fake device that mimics JetKVM's password mode."""

import asyncio

import pytest
from aiohttp import web

from jetkvm_cli.session import AuthError, JetKVMSession, RateLimited

PASSWORD = "correct horse"
TOKEN = "tok123"


async def _fake_device():
    seen = {"ws_cookies": []}

    async def login(request):
        pw = (await request.json()).get("password")
        if pw == "flood":
            return web.json_response({"error": "Too many attempts", "retry_after": 42}, status=429)
        if pw != PASSWORD:
            return web.json_response({"error": "Invalid password"}, status=401)
        resp = web.json_response({})
        resp.set_cookie("authToken", TOKEN, httponly=True, samesite="Strict")
        return resp

    async def signaling(request):
        if request.cookies.get("authToken") != TOKEN:
            return web.json_response({"error": "Authentication required"}, status=401)
        seen["ws_cookies"].append(request.cookies.get("authToken"))
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.close()  # end the session immediately; we only care that it was accepted
        return ws

    app = web.Application()
    app.router.add_post("/auth/login-local", login)
    app.router.add_get("/webrtc/signaling/client", signaling)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"127.0.0.1:{port}", seen


def _run(coro_fn):
    async def wrapper():
        runner, host, seen = await _fake_device()
        try:
            return await asyncio.wait_for(coro_fn(host, seen), 10)
        finally:
            await runner.cleanup()
    return asyncio.run(wrapper())


def test_missing_password_is_fatal_with_a_clear_message():
    async def go(host, _seen):
        with pytest.raises(AuthError, match="requires a password"):
            await JetKVMSession(host).run_forever()
    _run(go)


def test_wrong_password_is_fatal_not_retried():
    async def go(host, _seen):
        with pytest.raises(AuthError, match="wrong password"):
            await JetKVMSession(host, password="nope").run_forever()
    _run(go)


def test_correct_password_sends_cookie_to_signaling():
    # Also guards the CookieJar(unsafe=True) fix: the fake device is an IP host.
    async def go(host, seen):
        s = JetKVMSession(host, password=PASSWORD)
        try:
            await s._connect_once()
        finally:
            await s._teardown()
        assert seen["ws_cookies"] == [TOKEN]
    _run(go)


def test_rate_limit_reports_retry_after():
    async def go(host, _seen):
        s = JetKVMSession(host, password="flood")
        try:
            with pytest.raises(RateLimited) as exc:
                await s._connect_once()
            assert exc.value.retry_after == 42
        finally:
            await s._teardown()
    _run(go)
