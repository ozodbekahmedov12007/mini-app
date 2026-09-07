"""Persistent room allocation and chat. One service replica with a durable SQLite volume."""
import secrets
import sqlite3
import threading
import time


class Problem(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def admin(user):
    if not user.get("admin"):
        raise Problem("Faqat admin uchun", 403)


def integer(value, low, high):
    if isinstance(value, bool):
        raise Problem("Son noto‘g‘ri")
    try:
        number = int(value)
    except (ValueError, TypeError, OverflowError):
        raise Problem("Son noto‘g‘ri")
    if number < low or number > high:
        raise Problem(f"Qiymat {low}–{high} oralig‘ida bo‘lsin")
    return number


class Store:
    def __init__(self, path, clock=time.time):
        self.clock = clock
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS screenings (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
          movie_code INTEGER, asset_id TEXT NOT NULL, starts REAL NOT NULL, ends REAL NOT NULL,
          duration INTEGER NOT NULL, vip INTEGER NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0,
          created_by INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS rooms (
          id TEXT PRIMARY KEY, screening TEXT NOT NULL REFERENCES screenings(id),
          private INTEGER NOT NULL, owner INTEGER NOT NULL, position REAL NOT NULL DEFAULT 0,
          playing INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL, created REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS room_screening ON rooms(screening, private);
        CREATE TABLE IF NOT EXISTS members (
          user_id INTEGER PRIMARY KEY, room TEXT NOT NULL REFERENCES rooms(id),
          name TEXT NOT NULL, photo TEXT NOT NULL, seen REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS member_room ON members(room, seen);
        CREATE TABLE IF NOT EXISTS restrictions (
          user_id INTEGER PRIMARY KEY, until REAL NOT NULL, banned INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, user_id INTEGER NOT NULL,
          name TEXT NOT NULL, photo TEXT NOT NULL, text TEXT NOT NULL, asset_id TEXT,
          reply_to INTEGER, created REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS message_scope ON messages(scope, id);
        CREATE INDEX IF NOT EXISTS message_user_time ON messages(user_id, created);
        CREATE UNIQUE INDEX IF NOT EXISTS message_asset ON messages(asset_id) WHERE asset_id IS NOT NULL;
        CREATE TABLE IF NOT EXISTS assets (
          id TEXT PRIMARY KEY, owner INTEGER NOT NULL, kind TEXT NOT NULL, mime TEXT NOT NULL,
          size INTEGER NOT NULL, object_key TEXT NOT NULL, upload_id TEXT NOT NULL,
          ready INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS asset_owner_created ON assets(owner, created);
        CREATE INDEX IF NOT EXISTS screening_end ON screenings(cancelled, ends);
        CREATE TABLE IF NOT EXISTS reports (
          message_id INTEGER NOT NULL, user_id INTEGER NOT NULL, reason TEXT NOT NULL,
          created REAL NOT NULL, PRIMARY KEY(message_id, user_id));
        CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER NOT NULL,
          action TEXT NOT NULL, detail TEXT NOT NULL, created REAL NOT NULL);
        ''')

    def rows(self, sql, args=()):
        return [dict(row) for row in self.db.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.rows(sql, args)
        return rows[0] if rows else None

    def audit(self, user, action, detail):
        self.db.execute("INSERT INTO audit(admin_id,action,detail,created) VALUES(?,?,?,?)",
                        (user["id"], action, str(detail)[:500], self.clock()))

    def allowed(self, user, writing=False):
        row = self.one("SELECT * FROM restrictions WHERE user_id=?", (user["id"],))
        if row and row["until"] > self.clock() and (row["banned"] or writing):
            raise Problem("Sizga vaqtinchalik cheklov qo‘yilgan", 403)

    def screening(self, sid, user, active=True):
        self.allowed(user)
        row = self.one("SELECT * FROM screenings WHERE id=?", (sid,))
        if not row or row["cancelled"] or row["ends"] <= self.clock():
            raise Problem("Seans tugagan yoki topilmadi", 410)
        if active and row["starts"] > self.clock():
            raise Problem("Seans hali boshlanmadi", 409)
        if row["vip"] and user.get("vip_until", 0) <= self.clock():
            raise Problem("Bu kino uchun shaxsiy VIP obuna kerak", 403)
        return row

    def list_screenings(self, user):
        self.allowed(user)
        columns = "id,title,description,movie_code,starts,ends,duration,vip"
        return self.rows(f"SELECT {columns} FROM screenings WHERE cancelled=0 AND ends>? ORDER BY starts LIMIT 100",
                         (self.clock(),))

    def create_screening(self, user, data):
        admin(user)
        title = str(data.get("title", "")).strip()
        if not title or len(title) > 160:
            raise Problem("Kino nomi 1–160 belgi bo‘lsin")
        start = integer(data.get("starts"), int(self.clock()) - 60, int(self.clock()) + 366*86400)
        duration = integer(data.get("duration"), 60, 8*3600)
        asset = self.one("SELECT * FROM assets WHERE id=? AND ready=1 AND kind='movie'", (data.get("asset_id"),))
        if not asset:
            raise Problem("Avval kinoni yuklang")
        sid = secrets.token_urlsafe(12)
        code = integer(data["movie_code"], 1, 2147483647) if data.get("movie_code") else None
        self.db.execute("INSERT INTO screenings(id,title,description,movie_code,asset_id,starts,ends,duration,vip,created_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (sid, title, str(data.get("description", ""))[:1500], code, asset["id"], start,
                         start+duration, duration, int(bool(data.get("vip"))), user["id"]))
        self.audit(user, "create_screening", sid)
        return {"id": sid}

    def cancel(self, user, sid):
        admin(user)
        self.db.execute("UPDATE screenings SET cancelled=1 WHERE id=?", (sid,))
        self.audit(user, "cancel_screening", sid)
        return {"ok": True}

    def join(self, user, sid=None, room_id=None, private=False):
        # BEGIN IMMEDIATE also protects the capacity if another process opens this file.
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                now = self.clock()
                self.db.execute("DELETE FROM members WHERE seen<?", (now-75,))
                if room_id:
                    room = self.one("SELECT * FROM rooms WHERE id=?", (room_id,))
                    if not room:
                        raise Problem("Xona topilmadi", 404)
                    sid = room["screening"]
                self.screening(sid, user)
                self.db.execute("DELETE FROM members WHERE user_id=?", (user["id"],))
                if not room_id:
                    room = None if private else self.one('''SELECT r.* FROM rooms r LEFT JOIN members m ON m.room=r.id
                        WHERE r.screening=? AND r.private=0 GROUP BY r.id HAVING COUNT(m.user_id)<20
                        ORDER BY r.created LIMIT 1''', (sid,))
                    if not room:
                        # Bound room creation without charging users or sending any messages.
                        if self.one("SELECT COUNT(*) n FROM rooms WHERE owner=? AND created>?", (user["id"], now-3600))["n"] >= 10:
                            raise Problem("Bir soatda 10 ta xona yaratish mumkin", 429)
                        room_id = secrets.token_urlsafe(18)
                        self.db.execute("INSERT INTO rooms(id,screening,private,owner,updated,created) VALUES(?,?,?,?,?,?)",
                                        (room_id, sid, int(private), user["id"], now, now))
                        room = self.one("SELECT * FROM rooms WHERE id=?", (room_id,))
                room_id = room["id"]
                if self.one("SELECT COUNT(*) n FROM members WHERE room=?", (room_id,))["n"] >= 20:
                    raise Problem("Xona to‘lgan. Bo‘sh joy paydo bo‘lgach qayta kiring", 409)
                self.db.execute("INSERT INTO members VALUES(?,?,?,?,?)", (user["id"], room_id, user["name"], user.get("photo", ""), now))
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            return self.room(user, room_id)

    def room(self, user, rid, heartbeat=False):
        room = self.one("SELECT * FROM rooms WHERE id=?", (rid,))
        if not room:
            raise Problem("Xona topilmadi", 404)
        screening = self.screening(room["screening"], user)
        member = self.one("SELECT * FROM members WHERE user_id=? AND room=? AND seen>?", (user["id"], rid, self.clock()-75))
        if not member:
            raise Problem("Xonaga qayta kiring", 403)
        if heartbeat:
            self.db.execute("UPDATE members SET seen=? WHERE user_id=?", (self.clock(), user["id"]))
        members = self.rows("SELECT user_id,name,photo FROM members WHERE room=? AND seen>? ORDER BY user_id", (rid, self.clock()-75))
        if room["private"] and room["owner"] not in {m["user_id"] for m in members}:
            room["owner"] = members[0]["user_id"]
            self.db.execute("UPDATE rooms SET owner=? WHERE id=?", (room["owner"], rid))
        position = (room["position"] + (self.clock()-room["updated"] if room["playing"] else 0)
                    if room["private"] else self.clock()-screening["starts"])
        return {"id": rid, "screening_id": screening["id"], "title": screening["title"], "ends": screening["ends"],
                "duration": screening["duration"], "private": bool(room["private"]), "owner": room["owner"],
                "position": max(0, min(position, screening["duration"])),
                "playing": bool(room["playing"]) if room["private"] else True,
                "members": members, "server_time": self.clock()}

    def control(self, user, rid, data):
        room = self.room(user, rid, heartbeat=True)
        if not room["private"] or room["owner"] != user["id"]:
            raise Problem("Faqat shaxsiy xona egasi boshqaradi", 403)
        position = integer(data.get("position"), 0, room["duration"])
        self.db.execute("UPDATE rooms SET position=?,playing=?,updated=? WHERE id=?",
                        (position, int(bool(data.get("playing"))), self.clock(), rid))
        return self.room(user, rid)

    def leave(self, user):
        self.db.execute("DELETE FROM members WHERE user_id=?", (user["id"],))
        return {"ok": True}

    def scope(self, user, scope):
        self.allowed(user)
        if scope != "global":
            self.room(user, scope)

    def history(self, user, scope, before=None):
        self.scope(user, scope)
        upper = integer(before, 1, 2**63-1) if before else 2**63-1
        return list(reversed(self.rows("SELECT id,user_id,name,photo,text,asset_id,reply_to,created,deleted FROM messages WHERE scope=? AND id<? ORDER BY id DESC LIMIT 50", (scope, upper))))

    def send(self, user, scope, data):
        self.scope(user, scope)
        self.allowed(user, writing=True)
        text = str(data.get("text", "")).strip()
        aid = data.get("asset_id") or None
        if len(text) > 2000 or (not text and not aid):
            raise Problem("Xabar 1–2000 belgi bo‘lsin")
        if self.one("SELECT COUNT(*) n FROM messages WHERE user_id=? AND created>?", (user["id"], self.clock()-10))["n"] >= 5:
            raise Problem("Biroz kuting: 10 soniyada 5 ta xabar", 429)
        if aid:
            asset = self.one("SELECT * FROM assets WHERE id=? AND owner=? AND ready=1 AND kind IN ('voice','round') AND created>?", (aid, user["id"], self.clock()-7*86400))
            if not asset or self.one("SELECT id FROM messages WHERE asset_id=?", (aid,)):
                raise Problem("Media topilmadi yoki allaqachon yuborilgan")
        reply = integer(data["reply_to"], 1, 2**63-1) if data.get("reply_to") else None
        if reply and not self.one("SELECT id FROM messages WHERE id=? AND scope=? AND deleted=0", (reply, scope)):
            raise Problem("Javob berilayotgan xabar topilmadi")
        cursor = self.db.execute("INSERT INTO messages(scope,user_id,name,photo,text,asset_id,reply_to,created) VALUES(?,?,?,?,?,?,?,?)",
                                (scope, user["id"], user["name"], user.get("photo", ""), text, aid, reply, self.clock()))
        return {"id": cursor.lastrowid}

    def moderate(self, user, data):
        admin(user)
        if data.get("message_id"):
            mid = integer(data["message_id"], 1, 2**63-1)
            self.db.execute("UPDATE messages SET text='Xabar o‘chirilgan',asset_id=NULL,deleted=1 WHERE id=?", (mid,))
            self.audit(user, "delete_message", mid)
        elif data.get("user_id"):
            uid = integer(data["user_id"], 1, 2**63-1)
            hours = integer(data.get("hours", 24), 0, 8760)
            if uid == user["id"]:
                raise Problem("O‘zingizni cheklay olmaysiz")
            self.db.execute("INSERT INTO restrictions VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET until=excluded.until,banned=excluded.banned",
                            (uid, self.clock()+hours*3600, int(bool(data.get("banned")))))
            self.audit(user, "restrict_user", f"{uid}:{hours}")
        else:
            raise Problem("Xabar yoki foydalanuvchini tanlang")
        return {"ok": True}

    def report(self, user, scope, data):
        self.scope(user, scope)
        mid = integer(data.get("message_id"), 1, 2**63-1)
        if not self.one("SELECT id FROM messages WHERE id=? AND scope=? AND deleted=0", (mid, scope)):
            raise Problem("Xabar topilmadi", 404)
        self.db.execute("INSERT OR IGNORE INTO reports VALUES(?,?,?,?)", (mid, user["id"], str(data.get("reason", "Spam"))[:300], self.clock()))
        return {"ok": True}

    def dashboard(self, user):
        admin(user)
        return {"screenings": self.rows("SELECT id,title,starts,ends,vip,cancelled FROM screenings ORDER BY starts DESC LIMIT 100"),
                "reports": self.rows("SELECT r.*,m.text,m.name,m.user_id author_id FROM reports r JOIN messages m ON m.id=r.message_id WHERE m.deleted=0 ORDER BY r.created DESC LIMIT 50"),
                "online": self.one("SELECT COUNT(*) n FROM members WHERE seen>?", (self.clock()-75,))["n"],
                "audit": self.rows("SELECT * FROM audit ORDER BY id DESC LIMIT 30")}

    def call(self, method, *args, **kwargs):
        with self.lock:
            return getattr(self, method)(*args, **kwargs)
