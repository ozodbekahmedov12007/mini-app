"""Telegram sticker packs, with bounded downloads and no account credentials in URLs."""
import asyncio
import gzip
import json
import re
from collections import OrderedDict
from aiohttp import web
from miniapp.core import Problem

class TelegramStickers:
    def __init__(self,media):
        self.media=media
        self.packs=OrderedDict()
        self.files=OrderedDict()
        self.lock=asyncio.Lock()
        self.slots=asyncio.Semaphore(2)
        self.bytes=0

    def name(self,value):
        value=str(value).strip().rstrip('/').split('/')[-1]
        if not re.fullmatch(r'[A-Za-z0-9_]{1,64}',value):raise Problem('Telegram stiker yoki emoji to‘plami havolasini kiriting')
        return value

    async def pack(self,value):
        name=self.name(value)
        async with self.lock:
            if name not in self.packs:
                from telethon.tl.functions.messages import GetStickerSetRequest
                from telethon.tl.types import InputStickerSetShortName,InputStickerSetAnimatedEmoji
                client=await self.media.connect()
                try:
                    result=await asyncio.wait_for(client(GetStickerSetRequest(InputStickerSetAnimatedEmoji() if name=='animated_emoji' else InputStickerSetShortName(name),hash=0)),20)
                except Exception:raise Problem('To‘plam ochilmadi. Telegram havolasini tekshiring.',502)
                docs={str(d.id):d for d in result.documents[:200]}
                self.packs[name]=(result.set.title,docs)
                while len(self.packs)>24:self.packs.popitem(last=False)
            self.packs.move_to_end(name)
            title,docs=self.packs[name]
            return {'name':name,'title':title,'items':[{'id':key,'emoji':next((a.alt for a in d.attributes if hasattr(a,'alt')),'🙂'),'kind':'tgs' if d.mime_type=='application/x-tgsticker' else 'video' if d.mime_type=='video/webm' else 'image','url':f'/telegram-sticker/{name}/{key}'} for key,d in docs.items()]}

    async def file(self,request):
        name=self.name(request.match_info['pack']);ident=request.match_info['doc']
        # Only packs already fetched by an authenticated user are downloadable.
        if name not in self.packs or ident not in self.packs[name][1]:raise web.HTTPNotFound()
        key=(name,ident)
        async with self.slots:
            if key not in self.files:
                doc=self.packs[name][1][ident]
                if doc.size>1024*1024:raise web.HTTPRequestEntityTooLarge(max_size=1024*1024,actual_size=doc.size)
                client=await self.media.connect()
                data=await asyncio.wait_for(client.download_media(doc,bytes),25)
                if not data:raise web.HTTPBadGateway()
                kind=doc.mime_type
                if kind=='application/x-tgsticker':
                    import zlib
                    decoder=zlib.decompressobj(16+zlib.MAX_WBITS)
                    data=decoder.decompress(data,2*1024*1024)
                    if not decoder.eof:raise web.HTTPBadGateway()
                    json.loads(data);kind='application/json'
                if kind not in ('application/json','video/webm','image/webp','image/png'):raise web.HTTPUnsupportedMediaType()
                self.files[key]=(data,kind);self.bytes+=len(data)
                while self.bytes>16*1024*1024:
                    _,(old,_)=self.files.popitem(last=False);self.bytes-=len(old)
            data,kind=self.files[key];self.files.move_to_end(key)
        return web.Response(body=data,content_type=kind,headers={'Cache-Control':'public,max-age=3600','X-Content-Type-Options':'nosniff'})
