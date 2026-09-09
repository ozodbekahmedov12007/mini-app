"""Bounded shared Telegram chunk cache; permissions stay at the HTTP layer."""
import asyncio
from telethon.errors import FileReferenceExpiredError, FileReferenceInvalidError
from collections import OrderedDict

CHUNK = 512 * 1024

class VideoChunks:
    def __init__(self, media, max_bytes=24*1024*1024):
        self.media, self.max_bytes = media, max_bytes
        self.cache, self.pending = OrderedDict(), {}
        self.bytes = 0
        self.slots = asyncio.Semaphore(3)

    async def read(self, doc, offset, source=None):
        key=(doc.id, offset)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        if key not in self.pending:
            if len(self.pending)>=64:
                raise TimeoutError('Video read queue full')
            async def fetch():
                current_doc = doc
                async with self.slots:
                    for attempt in range(3):
                        iterator=None
                        try:
                            client=await self.media.connect()
                            iterator=client.iter_download(current_doc,offset=offset,limit=1,request_size=CHUNK,chunk_size=CHUNK)
                            data=bytes(await asyncio.wait_for(iterator.__anext__(),25))
                            if not data:raise IOError('Empty Telegram chunk')
                            self.cache[key]=data
                            self.bytes+=len(data)
                            while self.bytes>self.max_bytes:
                                _,old=self.cache.popitem(last=False);self.bytes-=len(old)
                            return data
                        except (FileReferenceExpiredError, FileReferenceInvalidError):
                            if source is None or attempt == 2:raise
                            current_doc = await self.media.refresh_document(source, current_doc)
                        except (TimeoutError, OSError):
                            if attempt==2:raise
                            await asyncio.sleep(.4*(attempt+1))
                        finally:
                            if iterator is not None:await iterator.close()
            task=asyncio.create_task(fetch());self.pending[key]=task
            def done(task):
                self.pending.pop(key,None)
                if not task.cancelled():task.exception()
            task.add_done_callback(done)
        return await asyncio.shield(self.pending[key])

    async def close(self):
        tasks=list(self.pending.values())
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        self.cache.clear();self.bytes=0
