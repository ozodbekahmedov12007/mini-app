"""Telegram movie streaming on the Mini App host; no full video downloads."""
import asyncio
import json
import os
import re
import secrets
import time
from collections import OrderedDict
from aiohttp import web
from miniapp.core import Problem, admin


def byte_range(value, size):
    if not value:
        return 0, size - 1, 200
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
    if not match or not any(match.groups()):
        raise web.HTTPRequestRangeNotSatisfiable(headers={"Content-Range": f"bytes */{size}"})
    first, last = match.groups()
    start = int(first) if first else max(0, size-int(last))
    end = min(size-1, int(last)) if first and last else size-1
    if start >= size or end < start:
        raise web.HTTPRequestRangeNotSatisfiable(headers={"Content-Range": f"bytes */{size}"})
    return start, end, 206


class TelegramMedia:
    def __init__(self):
        self.client = None
        self.lock = asyncio.Lock()
        self.metadata_lock = asyncio.Lock()
        self.docs = OrderedDict()
        self.tickets = {}
        self.active = {}

    @property
    def configured(self):
        return bool(os.getenv('TG_API_ID') and os.getenv('TG_API_HASH') and (os.getenv('TG_SESSION') or os.getenv('TG_BOT_TOKEN')))

    async def connect(self):
        if not self.configured:
            raise Problem('Telegram video ulanishi hali sozlanmagan', 503)
        async with self.lock:
            if self.client is None:
                from telethon import TelegramClient
                from telethon.sessions import StringSession
                client = TelegramClient(StringSession(os.getenv("TG_SESSION", "")), int(os.environ['TG_API_ID']),
                    os.environ['TG_API_HASH'], receive_updates=False,
                    request_retries=1, connection_retries=2, flood_sleep_threshold=0)
                try:
                    await asyncio.wait_for(client.connect(), 20)
                    if not await client.is_user_authorized():
                        if not os.getenv('TG_BOT_TOKEN'):
                            raise RuntimeError('Telegram session expired')
                        await asyncio.wait_for(client.sign_in(bot_token=os.environ['TG_BOT_TOKEN']), 30)
                except Exception:
                    await client.disconnect()
                    raise Problem('Telegram bilan video ulanishi amalga oshmadi', 503)
                self.client = client
        return self.client

    async def document(self, source):
        key = (int(source['channel']), int(source['message']))
        async with self.metadata_lock:
            cached = self.docs.get(key)
            if cached and cached[0] > time.monotonic():
                self.docs.move_to_end(key)
                return cached[1]
            client = await self.connect()
            try:
                async with asyncio.timeout(25):
                    peer = await client.get_input_entity(key[0])
                    message = await client.get_messages(peer, ids=key[1])
            except Exception:
                raise Problem('Telegram kanalidagi video ochilmadi. Bot kanalga kira olishini tekshiring.', 502)
            if not message or not message.document:
                raise Problem('Tanlangan xabarda video topilmadi', 404)
            doc = message.document
            if doc.mime_type != 'video/mp4':
                raise Problem('Bu video brauzer uchun MP4 formatida bo‘lishi kerak', 400)
            self.docs[key] = (time.monotonic()+300, doc)
            while len(self.docs) > 100:
                self.docs.popitem(last=False)
            return doc

    async def import_movie(self, app, user, source, data):
        from miniapp.server import db
        admin(user)
        doc = await self.document(source)
        seconds = next((int(a.duration) for a in doc.attributes if hasattr(a, 'duration')), 0)
        if not 60 <= seconds <= 28800:
            raise Problem('Kino davomiyligi 1 daqiqadan 8 soatgacha bo‘lishi kerak')
        aid = 'tg_'+secrets.token_urlsafe(18)
        key = json.dumps({'channel': source['channel'], 'message': source['message']})
        await db(app, 'rows', 'INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?)',
            (aid, user['id'], 'movie', 'video/mp4', doc.size, key, 'telegram', 1, time.time()))
        try:
            return await db(app, 'create_screening', user, {
                'title': source['title'], 'description': source.get('description', ''),
                'movie_code': source['code'], 'asset_id': aid,
                'starts': data.get('starts') or int(time.time())+5,
                'duration': seconds, 'vip': bool(source['vip'] or data.get('vip'))})
        except Exception:
            await db(app, 'rows', 'DELETE FROM assets WHERE id=?', (aid,))
            raise

    async def playback(self, app, user, rid, raw):
        from miniapp.server import db
        room = await db(app, 'room', user, rid, heartbeat=True)
        asset = await db(app, 'one', 'SELECT a.* FROM assets a JOIN screenings s ON s.asset_id=a.id WHERE s.id=?', (room['screening_id'],))
        if not asset or asset['upload_id'] != 'telegram':
            return None
        now = time.monotonic()
        self.tickets = {k: v for k, v in self.tickets.items() if v['until'] > now}
        if len(self.tickets) >= 4096:
            raise Problem('Video ulanishlari band', 429)
        ttl = max(1, min(120, int(room['ends']-time.time())))
        token = secrets.token_urlsafe(32)
        self.tickets[token] = {'until': now+ttl, 'raw': raw, 'rid': rid,
            'uid': user['id'], 'source': json.loads(asset['object_key'])}
        return {'url': '/telegram-video/'+token, 'expires_in': ttl, 'room': room}

    async def stream(self, request):
        from miniapp.server import db
        app = request.app
        ticket = self.tickets.get(request.match_info['token'])
        if not ticket or ticket['until'] <= time.monotonic():
            raise web.HTTPUnauthorized()
        if request.headers.get('Origin') and request.headers['Origin'] != app['origin']:
            raise web.HTTPForbidden()
        uid = ticket['uid']
        if sum(self.active.values()) >= 40 or self.active.get(uid, 0) >= 3:
            raise web.HTTPTooManyRequests()
        self.active[uid] = self.active.get(uid, 0)+1
        iterator = None
        response = None
        try:
            async def check():
                user = await app['bridge'].identity(ticket['raw'])
                return await db(app, 'room', user, ticket['rid'])
            await check()
            doc = await self.document(ticket['source'])
            start, end, status = byte_range(request.headers.get('Range'), doc.size)
            headers = {'Content-Type': 'video/mp4', 'Content-Length': str(end-start+1),
                'Accept-Ranges': 'bytes', 'Cache-Control': 'no-store',
                'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff'}
            if status == 206:
                headers['Content-Range'] = f'bytes {start}-{end}/{doc.size}'
            response = web.StreamResponse(status=status, headers=headers)
            await response.prepare(request)
            if request.method == 'HEAD':
                return response
            client = await self.connect()
            iterator = client.iter_download(doc, offset=start, request_size=512*1024)
            remaining = end-start+1
            checked = time.monotonic()
            while remaining:
                if time.monotonic()-checked > 5:
                    await check()
                    checked = time.monotonic()
                try:
                    chunk = await asyncio.wait_for(iterator.__anext__(), 30)
                except StopAsyncIteration:
                    break
                piece = bytes(chunk[:remaining])
                await asyncio.wait_for(response.write(piece), 30)
                remaining -= len(piece)
            await response.write_eof()
            return response
        except (Exception, asyncio.CancelledError):
            if response is not None and response.prepared:
                if request.transport:
                    request.transport.close()
                return response
            raise
        finally:
            if iterator is not None:
                await iterator.close()
            self.active[uid] -= 1
            if not self.active[uid]:
                del self.active[uid]

    async def close(self):
        if self.client is not None:
            await self.client.disconnect()
