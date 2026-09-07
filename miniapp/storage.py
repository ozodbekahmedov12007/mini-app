"""Only signs and checks S3 operations. Media bytes never traverse this service."""
import math
import os
import secrets
import time
from miniapp.core import Problem, admin, integer

PART_SIZE = 32 * 1024 * 1024
LIMITS = {"movie": 8 * 1024**3, "voice": 8 * 1024**2, "round": 20 * 1024**2}
MIMES = {"movie": {"video/mp4"}, "voice": {"audio/webm", "audio/mp4", "audio/ogg"},
         "round": {"video/webm", "video/mp4"}}


class Storage:
    def __init__(self, store, client=None, bucket=None):
        self.store = store
        self.bucket = bucket or os.getenv("S3_BUCKET", "")
        self.client = client
        if not self.client and self.bucket:
            import boto3
            from botocore.config import Config
            self.client = boto3.client("s3", endpoint_url=os.getenv("S3_ENDPOINT") or None,
                region_name=os.getenv("S3_REGION", "auto"),
                config=Config(signature_version="s3v4", connect_timeout=5, read_timeout=20,
                              retries={"max_attempts": 2}))

    def ready(self):
        if not self.client or not self.bucket:
            raise Problem("Media xizmati hali ulanmagan. Admin sozlamalarni yakunlashi kerak", 503)

    def begin(self, user, data):
        self.ready()
        kind = data.get("kind")
        mime = str(data.get("mime", "")).split(";")[0]
        if kind not in LIMITS or mime not in MIMES[kind]:
            raise Problem("Kino: MP4. Ovoz/video: MP4, WebM yoki audio OGG")
        if kind == "movie":
            admin(user)
        with self.store.lock:
            self.store.allowed(user, writing=True)
            if self.store.one("SELECT COUNT(*) n FROM assets WHERE owner=? AND created>?", (user["id"], time.time()-3600))["n"] >= (20 if user.get("admin") else 12):
                raise Problem("Soatlik yuklash limiti tugadi", 429)
        size = integer(data.get("size"), 1, LIMITS[kind])
        aid = secrets.token_urlsafe(18)
        key = f"{'movies' if kind == 'movie' else 'chat'}/{aid}"
        result = self.client.create_multipart_upload(Bucket=self.bucket, Key=key, ContentType=mime,
                                                     CacheControl="private, max-age=0")
        with self.store.lock:
            self.store.db.execute("INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?)",
                (aid, user["id"], kind, mime, size, key, result["UploadId"], 0, time.time()))
        return {"id": aid, "part_size": PART_SIZE, "parts": math.ceil(size/PART_SIZE)}

    def asset(self, user, aid):
        with self.store.lock:
            self.store.allowed(user, writing=True)
            asset = self.store.one("SELECT * FROM assets WHERE id=? AND owner=?", (aid, user["id"]))
        if not asset or asset["created"] < time.time()-86400:
            raise Problem("Yuklash muddati tugagan", 404)
        if asset["kind"] == "movie":
            admin(user)
        return asset

    def part(self, user, aid, number):
        self.ready()
        asset = self.asset(user, aid)
        if asset["ready"]:
            raise Problem("Yuklash tugallangan", 409)
        number = integer(number, 1, math.ceil(asset["size"]/PART_SIZE))
        length = min(PART_SIZE, asset["size"]-(number-1)*PART_SIZE)
        url = self.client.generate_presigned_url("upload_part", Params={"Bucket": self.bucket,
            "Key": asset["object_key"], "UploadId": asset["upload_id"], "PartNumber": number,
            "ContentLength": length}, ExpiresIn=900)
        return {"url": url}

    def complete(self, user, aid):
        self.ready()
        asset = self.asset(user, aid)
        if asset["ready"]:
            return {"id": aid, "kind": asset["kind"]}
        # List actual uploaded parts rather than trusting browser sizes/ETags.
        parts = self.client.list_parts(Bucket=self.bucket, Key=asset["object_key"], UploadId=asset["upload_id"]).get("Parts", [])
        count = math.ceil(asset["size"]/PART_SIZE)
        if len(parts) != count or any(p["PartNumber"] != i+1 or p["Size"] != min(PART_SIZE, asset["size"]-i*PART_SIZE) for i,p in enumerate(parts)):
            raise Problem("Fayl to‘liq yuklanmagan. Qayta urinib ko‘ring")
        self.client.complete_multipart_upload(Bucket=self.bucket, Key=asset["object_key"], UploadId=asset["upload_id"],
            MultipartUpload={"Parts": [{"PartNumber": p["PartNumber"], "ETag": p["ETag"]} for p in parts]})
        with self.store.lock:
            self.store.db.execute("UPDATE assets SET ready=1 WHERE id=?", (aid,))
        return {"id": aid, "kind": asset["kind"]}

    def link(self, key, ttl=60):
        self.ready()
        return self.client.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=ttl)

    def playback(self, user, rid):
        self.ready()
        with self.store.lock:
            room = self.store.room(user, rid, heartbeat=True)
            asset = self.store.one("SELECT a.* FROM assets a JOIN screenings s ON s.asset_id=a.id WHERE s.id=? AND a.ready=1", (room["screening_id"],))
        if not asset:
            raise Problem("Video tayyor emas", 404)
        ttl = max(1, min(120, int(room["ends"]-time.time())))
        return {"url": self.link(asset["object_key"], ttl), "expires_in": ttl, "room": room}

    def message_media(self, user, mid):
        self.ready()
        with self.store.lock:
            row = self.store.one("SELECT m.scope,a.* FROM messages m JOIN assets a ON a.id=m.asset_id WHERE m.id=? AND m.deleted=0 AND a.ready=1", (mid,))
            if not row:
                raise Problem("Media topilmadi", 404)
            self.store.scope(user, row["scope"])
            if row["created"] < time.time()-7*86400:
                raise Problem("Media saqlash muddati tugagan (7 kun)", 410)
        return {"url": self.link(row["object_key"], 120), "kind": row["kind"]}

    def cleanup(self):
        """Delete only objects belonging to this app after expiry; keep text chat."""
        if not self.client:
            return
        now = time.time()
        with self.store.lock:
            expired = self.store.rows('''SELECT * FROM assets a WHERE
                (ready=0 AND created<?) OR (kind!='movie' AND created<?) OR
                (kind='movie' AND created<? AND NOT EXISTS
                  (SELECT 1 FROM screenings s WHERE s.asset_id=a.id AND cancelled=0 AND ends>?)) LIMIT 20''',
                (now-86400, now-7*86400, now-86400, now))
        for asset in expired:
            if asset["ready"]:
                self.client.delete_object(Bucket=self.bucket, Key=asset["object_key"])
            else:
                self.client.abort_multipart_upload(Bucket=self.bucket, Key=asset["object_key"], UploadId=asset["upload_id"])
            with self.store.lock:
                self.store.db.execute("DELETE FROM assets WHERE id=?", (asset["id"],))
