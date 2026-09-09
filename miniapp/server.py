import asyncio
import hashlib
import json
import logging
import os
import secrets
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlparse
import aiohttp
from aiohttp import web
from miniapp.core import Store, Problem, admin
from miniapp.storage import Storage
from miniapp.telegram_media import TelegramMedia
from miniapp.telegram_stickers import TelegramStickers

log = logging.getLogger("miniapp")
STATIC = Path(__file__).parent / "static"


class Bridge:
    def __init__(self, session):
        self.session = session
        self.cache = OrderedDict()
        self.url = os.getenv("BOT_BRIDGE_URL", "").rstrip("/")
        self.secret = os.getenv("MINIAPP_BRIDGE_SECRET", "")
        self.pending = {}
        self.slots = asyncio.Semaphore(8)

    async def request(self, path, raw, **extra):
        if not self.url or len(self.secret) < 32 or self.secret.startswith(("REPLACE_", "PASTE_")):
            raise Problem("Bot bilan ulanish hali sozlanmagan", 503)
        try:
            async with self.session.post(self.url+"/miniapp/"+path,
                headers={"Authorization": "Bearer "+self.secret},
                json={"init_data": raw, **extra}) as response:
                if response.status in (401,403):
                    raise Problem("Telegram orqali qayta kiring yoki ruxsatingizni tekshiring", response.status)
                if response.status == 404:
                    raise Problem("Kino manbasi topilmadi. Admin botdagi kino faylini tekshirsin.",404)
                if response.status != 200:
                    raise Problem("Bot bilan aloqa vaqtincha ishlamayapti", 503)
                return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise Problem("Bot bilan aloqa vaqtincha ishlamayapti", 503)

    async def identity(self, raw):
        key = hashlib.sha256(raw.encode()).hexdigest()
        hit = self.cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        if key not in self.pending:
            if len(self.pending) >= 64:
                raise Problem("Kirish navbati band. Biroz kuting", 429)
            async def fetch():
                try:
                    async with self.slots:
                        result = await self.request("identity", raw)
                    self.cache[key] = (time.monotonic()+30, result)
                    self.cache.move_to_end(key)
                    while len(self.cache) > 2048:
                        self.cache.popitem(last=False)
                    return result
                finally:
                    self.pending.pop(key, None)
            self.pending[key] = asyncio.create_task(fetch())
        return await asyncio.shield(self.pending[key])


@web.middleware
async def errors(request, handler):
    try:
        response = await handler(request)
    except Problem as exc:
        response = web.json_response({"error": str(exc)}, status=exc.status)
    except (ValueError, TypeError, KeyError, OverflowError):
        response = web.json_response({"error": "So‘rov noto‘g‘ri"}, status=400)
    except web.HTTPException:
        raise
    except Exception:
        # No request body, tokens, presigned URLs or chat text in logs.
        log.exception("Mini App request failed: %s", request.path)
        response = web.json_response({"error": "Vaqtinchalik xato. Qayta urinib ko‘ring"}, status=503)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store" if request.path.startswith("/api") else "no-cache"
    response.headers["Permissions-Policy"] = "camera=(self), microphone=(self)"
    return response


async def identity(request):
    origin = request.headers.get("Origin")
    if origin and origin != request.app["origin"]:
        raise Problem("Boshqa saytdan so‘rovga ruxsat yo‘q", 403)
    raw = request.headers.get("X-Telegram-Init-Data", "")
    if not raw or len(raw)>16384:
        raise Problem("Mini Appni Telegram bot orqali oching", 401)
    # Bound unauthenticated bursts before hitting the bot bridge.
    throttle(request.app, "token:"+hashlib.sha256(raw.encode()).hexdigest(), 300)
    user = await request.app["bridge"].identity(raw)
    throttle(request.app, "user:"+str(user["id"]), 180)
    await db(request.app, "allowed", user)
    return user


def throttle(app, key, limit):
    now = time.monotonic()
    count, until = app["rates"].get(key, (0, now+60))
    if until <= now:
        count, until = 0, now+60
    if count >= limit:
        raise Problem("So‘rovlar ko‘paydi. Bir daqiqa kuting", 429)
    app["rates"][key] = (count+1, until)
    app["rates"].move_to_end(key)
    while len(app["rates"]) > 8192:
        app["rates"].popitem(last=False)


async def db(app, method, *args, **kwargs):
    return await asyncio.to_thread(app["store"].call, method, *args, **kwargs)


async def emit(app, scope=None):
    # Coalesce refreshes for slow clients; one pending notification per socket.
    for ws, item in list(app["sockets"].items()):
        if scope is None or item["scope"] == scope:
            item["event"].set()


async def api(request):
    user = await identity(request)
    app = request.app
    path = request.match_info.get("path", "")
    data = await request.json() if request.method == "POST" else {}
    if not isinstance(data, dict):
        raise Problem("So‘rov obyekt bo‘lishi kerak")
    result = None
    if request.method == "GET":
        if path == "me":
            await db(app, "profile_identity", user)
            result = {"user": user, "bot_username": app["bot_username"], "media_ready": bool(app["storage"].bucket), "telegram_ready": app["telegram"].configured}
        elif path == "screenings":
            result = {"screenings": await db(app, "list_screenings", user)}
        elif path == "room":
            result = await db(app, "room", user, request.query.get("id", ""), heartbeat=True)
        elif path == "messages":
            result = {"messages": await db(app, "history", user, request.query.get("scope", "global"), request.query.get("before"))}
        elif path == "playback":
            result = await app["telegram"].playback(app, user, request.query.get("room", ""), request.headers["X-Telegram-Init-Data"],request.query.get("quality","original"))
            if result is None:
                result = await asyncio.to_thread(app["storage"].playback, user, request.query.get("room", ""))
        elif path == "media":
            result = await asyncio.to_thread(app["storage"].message_media, user, int(request.query.get("id", 0)))
        elif path == "admin":
            result = await db(app, "dashboard", user)
        elif path == "admin/room":
            result = await db(app, "admin_room", user, request.query.get("id", ""))
        elif path == "cabinets":
            result = {"rooms": await db(app, "my_cabinets", user)}
        elif path == "social":
            result = await db(app, "social", user)
        elif path == "poll":
            result = await db(app, "poll", user, request.query.get("room", ""))
        elif path == "diagnostics":
            admin(user)
            result = {"errors": await db(app, "diagnostic_list", user),
                      "telegram_configured": app["telegram"].configured,
                      "uploads_configured": bool(app["storage"].bucket),
                      "streams": sum(app["telegram"].active.values())}
        elif path == "video-quality":
            room=await db(app,"room",user,request.query.get("room",""))
            asset=await db(app,"one","SELECT object_key,upload_id FROM assets WHERE id=(SELECT asset_id FROM screenings WHERE id=?)",(room["screening_id"],))
            result=app["telegram"].quality.status(json.loads(asset["object_key"])) if asset and asset["upload_id"]=="telegram" else {"ready":[],"enabled":False,"state":"unavailable"}
        elif path == "stickers":
            throttle(app,"stickers:"+str(user["id"]),15)
            result=await app["stickers"].pack(request.query.get("pack","animated_emoji"))
        elif path == "catalog":
            throttle(app, "catalog:"+str(user["id"]), 20)
            result = await app["bridge"].request("catalog", request.headers["X-Telegram-Init-Data"], query=request.query.get("q", ""))
    elif request.method == "POST":
        if path == "video-quality/prepare":
            admin(user)
            throttle(app,"quality-prepare:"+str(user["id"]),3)
            room=await db(app,"room",user,str(data.get("room","")))
            asset=await db(app,"one","SELECT object_key,upload_id FROM assets WHERE id=(SELECT asset_id FROM screenings WHERE id=?)",(room["screening_id"],))
            if not asset or asset["upload_id"]!="telegram":raise Problem("Telegram kinosini tanlang")
            result=await app["telegram"].quality.prepare(json.loads(asset["object_key"]))
        elif path == "cabinet/create":
            throttle(app,"cabinet-create:"+str(user["id"]),5)
            source=await app["bridge"].request("source",request.headers["X-Telegram-Init-Data"],code=data.get("code"))
            result=await app["telegram"].create_cabinet(app,user,source,data)
        elif path == "cabinet/finish":
            result=await db(app,"finish_cabinet",user,str(data.get("room","")))
            await emit(app,data.get("room"))
        elif path == "cabinet/close":
            result=await db(app,"close_cabinet",user,str(data.get("room","")))
            await emit(app,data.get("room"))
        elif path == "join":
            if data.get("private"):
                raise Problem("Kabinet yaratish formasidan kino va nom tanlang",409)
            result = await db(app, "join", user, data.get("screening"), data.get("room"), bool(data.get("private")), bool(data.get("locked")))
        elif path == "friend":
            throttle(app, "friend:"+str(user["id"]), 20)
            result = await db(app, "friend_action", user, data)
        elif path == "favorite":
            result = await db(app, "favorite", user, data)
        elif path == "room/request":
            throttle(app, "admission:"+str(user["id"]), 20)
            result = await db(app, "request_access", user, str(data.get("room", "")))
            await emit(app, data.get("room"))
        elif path == "room/access":
            result = await db(app, "access_action", user, data)
            await emit(app, data.get("room"))
        elif path == "room/invite":
            throttle(app, "invite:"+str(user["id"]), 20)
            result = await db(app, "invite_friend", user, data)
        elif path == "vote":
            result = await db(app, "vote", user, data)
            await emit(app, data.get("room"))
        elif path == "diagnostic":
            throttle(app, "diagnostic:"+str(user["id"]), 10)
            result = await db(app, "diagnostic", user, data)
        elif path == "diagnostics/check":
            admin(user)
            throttle(app, "media-check:"+str(user["id"]), 2)
            try:
                client = await app["telegram"].connect()
                valid = await asyncio.wait_for(client.is_user_authorized(), 10)
                result = {"ok": bool(valid), "message": "Telegram sessiyasi ishlayapti" if valid else "Telegram sessiyasini yangilang"}
            except Exception:
                result = {"ok": False, "message": "Telegramga ulanish tekshiruvdan o‘tmadi. TG sozlamalari va tarmoqni tekshiring."}
        elif path == "leave":
            result = await db(app, "leave", user)
        elif path == "control":
            result = await db(app, "control", user, data.get("room", ""), data)
            await emit(app, data.get("room"))
        elif path == "message":
            scope = str(data.get("scope", "global"))
            result = await db(app, "send", user, scope, data)
            await emit(app, scope)
        elif path == "report":
            result = await db(app, "report", user, data.get("scope", "global"), data)
        elif path == "screening/telegram":
            admin(user)
            throttle(app, "telegram-import:"+str(user["id"]), 10)
            source = await app["bridge"].request("source", request.headers["X-Telegram-Init-Data"], code=data.get("code"))
            result = await app["telegram"].import_movie(app, user, source, data)
            await emit(app)
        elif path == "screening":
            result = await db(app, "create_screening", user, data)
            await emit(app)
        elif path == "cancel":
            result = await db(app, "cancel", user, data.get("id"))
            await emit(app)
        elif path == "moderate":
            result = await db(app, "moderate", user, data)
            await emit(app)
        elif path == "upload/start":
            result = await asyncio.to_thread(app["storage"].begin, user, data)
        elif path == "upload/part":
            result = await asyncio.to_thread(app["storage"].part, user, data.get("id"), data.get("number"))
        elif path == "upload/complete":
            result = await asyncio.to_thread(app["storage"].complete, user, data.get("id"))
        elif path == "socket-ticket":
            scope = data.get("scope", "global")
            await db(app, "scope", user, scope)
            now = time.monotonic()
            for token, value in list(app["tickets"].items()):
                if value[0] < now:
                    del app["tickets"][token]
            if len(app["tickets"]) >= 4096:
                raise Problem("Ulanishlar band. Qayta urinib ko‘ring", 429)
            ticket = secrets.token_urlsafe(24)
            app["tickets"][ticket] = (now+20, request.headers["X-Telegram-Init-Data"], scope, user["id"])
            result = {"ticket": ticket}
    if result is None:
        raise web.HTTPNotFound()
    return web.json_response(result)


async def socket(request):
    if request.headers.get("Origin") != request.app["origin"]:
        raise web.HTTPForbidden()
    app = request.app
    ticket = app["tickets"].pop(request.query.get("ticket", ""), None)
    if not ticket or ticket[0] < time.monotonic():
        raise web.HTTPUnauthorized()
    _, raw, scope, uid = ticket
    if sum(item["uid"] == uid for item in app["sockets"].values()) >= 3:
        raise web.HTTPTooManyRequests()
    ws = web.WebSocketResponse(heartbeat=25, max_msg_size=1024, compress=False)
    await ws.prepare(request)
    event = asyncio.Event()
    app["sockets"][ws] = {"scope": scope, "uid": uid, "event": event}

    async def pump():
        while not ws.closed:
            try:
                await asyncio.wait_for(event.wait(), timeout=20)
            except asyncio.TimeoutError:
                pass
            changed = event.is_set()
            event.clear()
            try:
                user = await app["bridge"].identity(raw)
                await db(app, "scope", user, scope)
                await asyncio.wait_for(ws.send_json({"type": "refresh", "changed": changed}), timeout=5)
            except (Problem, asyncio.TimeoutError):
                await ws.close(code=4001, message=b"Rejoin required")
                return
            await asyncio.sleep(.2)
    task = asyncio.create_task(pump())
    try:
        async for _ in ws:
            pass  # Writes go through authenticated, rate-limited HTTP endpoints.
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        app["sockets"].pop(ws, None)
    return ws


async def lifecycle(app):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        if "bridge" not in app:
            app["bridge"] = Bridge(session)

        async def clean():
            while True:
                await asyncio.sleep(3600)
                try:
                    await asyncio.to_thread(app["storage"].cleanup)
                    await db(app, "rows", "DELETE FROM members WHERE seen<?", (time.time()-75,))
                except Exception:
                    log.exception("Media cleanup failed")
        task = asyncio.create_task(clean())
        yield
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*(ws.close(code=1001) for ws in list(app["sockets"])))
        await app["telegram"].close()
        app["store"].db.close()


async def health(request):
    return web.json_response({"ok": True})


async def index(request):
    return web.FileResponse(STATIC / "index.html")


def create_app(store=None, bridge=None, storage=None, origin=None):
    app = web.Application(middlewares=[errors], client_max_size=20000)
    app["store"] = store or Store(os.getenv("MINIAPP_DB", "/data/miniapp.sqlite"))
    app["storage"] = storage or Storage(app["store"])
    app["origin"] = (origin or os.getenv("PUBLIC_ORIGIN", "http://127.0.0.1:8080")).rstrip("/")
    app["bot_username"] = os.getenv("BOT_USERNAME", "").lstrip("@")
    app["sockets"], app["tickets"] = {}, {}
    app["rates"] = OrderedDict()
    app["telegram"] = TelegramMedia()
    app["stickers"] = TelegramStickers(app["telegram"])
    if bridge:
        app["bridge"] = bridge
    app.cleanup_ctx.append(lifecycle)
    app.router.add_get("/telegram-video/{token}", app["telegram"].stream)
    app.router.add_get("/telegram-sticker/{pack}/{doc}",app["stickers"].file)
    app.router.add_get("/health", health)
    app.router.add_route("*", "/api/{path:.*}", api)
    app.router.add_get("/ws", socket)
    app.router.add_get("/", index)
    app.router.add_static("/static/", STATIC, show_index=False)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=int(os.getenv("PORT", "8080")), access_log=None)
