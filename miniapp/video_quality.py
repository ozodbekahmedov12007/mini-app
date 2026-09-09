"""One bounded background transcode at a time. Only completed MP4s are served."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from miniapp.core import Problem
from miniapp.video_chunks import CHUNK

HEIGHTS=(360,480,720,1080)
RATES={360:450,480:750,720:1500,1080:3000}

class VideoQuality:
    def __init__(self, media, root=None):
        self.media=media
        self.root=Path(root or os.getenv('VIDEO_CACHE_DIR','/data/video-quality'))
        self.task=None
        self.statuses={}
        self.quota=int(os.getenv('VIDEO_CACHE_BYTES',str(8*1024**3)))

    def key(self, source):
        return hashlib.sha256(json.dumps(source,sort_keys=True).encode()).hexdigest()[:32]

    def path(self,source,height):
        if height not in HEIGHTS:raise Problem('Noto‘g‘ri video sifati')
        return self.root/self.key(source)/f'{height}.mp4'

    def status(self,source):
        return {'ready':[h for h in HEIGHTS if self.path(source,h).is_file()],
                'state':self.statuses.get(self.key(source),'idle'),
                'enabled':bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))}

    async def prepare(self,source):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            raise Problem('Video sifatlarini tayyorlash serverda hali o‘rnatilmagan',503)
        key=self.key(source)
        if self.statuses.get(key)=='ready':return self.status(source)
        if self.task and not self.task.done():
            if self.statuses.get(key) in ('downloading','encoding'):return self.status(source)
            raise Problem('Boshqa kino tayyorlanmoqda. Keyinroq urinib ko‘ring.',429)
        self.statuses[key]='downloading'
        self.task=asyncio.create_task(self.build(source))
        return self.status(source)

    async def run_process(self,*args):
        process=await asyncio.create_subprocess_exec(*args,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        try:
            output,_=await asyncio.wait_for(process.communicate(),8*3600)
            if process.returncode:raise RuntimeError('Video conversion failed')
            return output
        finally:
            if process.returncode is None:
                process.kill();await process.wait()

    async def build(self,source):
        key=self.key(source);folder=self.root/key;original=folder/'source.part.mp4'
        try:
            doc=await self.media.document(source)
            duration=next((int(a.duration) for a in doc.attributes if hasattr(a,'duration')),0)
            if not 1<=duration<=28800:raise ValueError('Duration unsupported')
            self.root.mkdir(parents=True,exist_ok=True)
            # Reserve for source, output renditions and faststart's temporary copy.
            reserve=doc.size+duration*sum(RATES.values())*1000//8+1024**3
            used=sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file())
            if reserve+used>self.quota or shutil.disk_usage(self.root).free<reserve+512*1024**2:
                self.statuses[key]='disk_full';return
            folder.mkdir(exist_ok=True)
            with original.open('wb') as output:
                for offset in range(0,doc.size,CHUNK):
                    chunk=await self.media.chunks.read(doc,offset)
                    await asyncio.to_thread(output.write,chunk[:doc.size-offset])
            info=json.loads(await self.run_process('ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=width,height','-of','json',str(original)))
            height=int(info['streams'][0]['height'])
            self.statuses[key]='encoding'
            for target in HEIGHTS:
                final=self.path(source,target)
                if target>height or final.exists():continue
                pending=folder/f'{target}.part.mp4';rate=RATES[target]
                await self.run_process('ffmpeg','-nostdin','-v','error','-y','-threads','1','-i',str(original),
                    '-map','0:v:0','-map','0:a:0?','-sn','-dn','-vf',f'scale=-2:{target}',
                    '-c:v','libx264','-preset','veryfast','-threads','1','-filter_threads','1',
                    '-b:v',f'{rate}k','-maxrate',f'{rate}k','-bufsize',f'{rate*2}k',
                    '-pix_fmt','yuv420p','-c:a','aac','-b:a','64k','-ac','2',
                    '-movflags','+faststart',str(pending))
                pending.replace(final)
            self.statuses[key]='ready'
        except asyncio.CancelledError:
            self.statuses[key]='interrupted';raise
        except Exception:
            self.statuses[key]='failed'
        finally:
            original.unlink(missing_ok=True)
            if folder.exists():
                for p in folder.glob('*.part.mp4'):p.unlink(missing_ok=True)
            if len(self.statuses)>100:
                for old in list(self.statuses)[:-100]:self.statuses.pop(old,None)

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
