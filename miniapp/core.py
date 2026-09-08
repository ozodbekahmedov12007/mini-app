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
        CREATE TABLE IF NOT EXISTS screening_visitors (
          screening TEXT NOT NULL REFERENCES screenings(id), user_id INTEGER NOT NULL,
          first_seen REAL NOT NULL, last_seen REAL NOT NULL,
          PRIMARY KEY(screening,user_id));
        CREATE TABLE IF NOT EXISTS profiles (
          user_id INTEGER PRIMARY KEY, name TEXT NOT NULL, photo TEXT NOT NULL,
          friend_code TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS friendships (
          sender INTEGER NOT NULL, recipient INTEGER NOT NULL, accepted INTEGER NOT NULL DEFAULT 0,
          created REAL NOT NULL, PRIMARY KEY(sender,recipient));
        CREATE INDEX IF NOT EXISTS friend_recipient ON friendships(recipient,accepted);
        CREATE TABLE IF NOT EXISTS room_access (
          room TEXT NOT NULL REFERENCES rooms(id), user_id INTEGER NOT NULL,
          name TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL,
          PRIMARY KEY(room,user_id));
        CREATE TABLE IF NOT EXISTS room_invitations (
          room TEXT NOT NULL REFERENCES rooms(id), user_id INTEGER NOT NULL,
          sender INTEGER NOT NULL, created REAL NOT NULL, PRIMARY KEY(room,user_id));
        CREATE INDEX IF NOT EXISTS invitation_user ON room_invitations(user_id);
        CREATE TABLE IF NOT EXISTS favorites (
          user_id INTEGER NOT NULL, screening TEXT NOT NULL REFERENCES screenings(id),
          created REAL NOT NULL, PRIMARY KEY(user_id,screening));
        CREATE INDEX IF NOT EXISTS visitor_user ON screening_visitors(user_id,last_seen);
        CREATE TABLE IF NOT EXISTS movie_votes (
          room TEXT NOT NULL REFERENCES rooms(id), user_id INTEGER NOT NULL,
          screening TEXT NOT NULL REFERENCES screenings(id), PRIMARY KEY(room,user_id));
        CREATE TABLE IF NOT EXISTS diagnostics (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
          room TEXT NOT NULL, kind TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER NOT NULL,
          action TEXT NOT NULL, detail TEXT NOT NULL, created REAL NOT NULL);
        ''')
        if 'locked' not in {row[1] for row in self.db.execute('PRAGMA table_info(rooms)')}:
            self.db.execute('ALTER TABLE rooms ADD COLUMN locked INTEGER NOT NULL DEFAULT 0')

        if 'personal' not in {row[1] for row in self.db.execute('PRAGMA table_info(screenings)')}:
            self.db.execute('ALTER TABLE screenings ADD COLUMN personal INTEGER NOT NULL DEFAULT 0')
        if 'name' not in {row[1] for row in self.db.execute('PRAGMA table_info(rooms)')}:
            self.db.execute("ALTER TABLE rooms ADD COLUMN name TEXT NOT NULL DEFAULT ''")

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
        columns = "id,title,description,movie_code,starts,ends,duration,vip,EXISTS(SELECT 1 FROM favorites f WHERE f.screening=screenings.id AND f.user_id=?) saved"
        return self.rows(f"SELECT {columns} FROM screenings WHERE personal=0 AND cancelled=0 AND ends>? ORDER BY starts LIMIT 100",
                         (user["id"],self.clock()))

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

    def join(self, user, sid=None, room_id=None, private=False, locked=False):
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
                    if room["locked"] and room["owner"] != user["id"]:
                        permission = self.one("SELECT status FROM room_access WHERE room=? AND user_id=?", (room_id,user["id"]))
                        if not permission or permission["status"] != "approved":
                            raise Problem("Kabinetga kirish uchun egasining ruxsati kerak", 403)
                screening = self.screening(sid, user)
                if screening['personal'] and not room_id:
                    raise Problem('Shaxsiy kabinetga taklif orqali kiring',403)
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
                        if private and locked:
                            self.db.execute("UPDATE rooms SET locked=1 WHERE id=?", (room_id,))
                        room = self.one("SELECT * FROM rooms WHERE id=?", (room_id,))
                room_id = room["id"]
                if self.one("SELECT COUNT(*) n FROM members WHERE room=?", (room_id,))["n"] >= 20:
                    raise Problem("Xona to‘lgan. Bo‘sh joy paydo bo‘lgach qayta kiring", 409)
                self.db.execute("INSERT INTO members VALUES(?,?,?,?,?)", (user["id"], room_id, user["name"], user.get("photo", ""), now))
                self.db.execute("INSERT INTO screening_visitors VALUES(?,?,?,?) ON CONFLICT(screening,user_id) DO UPDATE SET last_seen=excluded.last_seen",
                                (sid,user["id"],now,now))
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
        if room["private"] and not room["locked"] and room["owner"] not in {m["user_id"] for m in members}:
            room["owner"] = members[0]["user_id"]
            self.db.execute("UPDATE rooms SET owner=? WHERE id=?", (room["owner"], rid))
        position = (room["position"] + (self.clock()-room["updated"] if room["playing"] else 0)
                    if room["private"] else self.clock()-screening["starts"])
        return {"id": rid, "screening_id": screening["id"], "title": room["name"] or screening["title"], "movie_title": screening["title"], "personal": bool(screening["personal"]), "ends": screening["ends"],
                "duration": screening["duration"], "locked": bool(room["locked"]), "private": bool(room["private"]), "owner": room["owner"],
                "position": max(0, min(position, screening["duration"])),
                "playing": bool(room["playing"]) if room["private"] else True,
                "members": members, "server_time": self.clock(),
                "requests": self.rows("SELECT user_id,name,status FROM room_access WHERE room=? AND status='pending' ORDER BY created LIMIT 30", (rid,)) if room["owner"] == user["id"] else []}

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

    def admin_room(self, user, rid):
        admin(user)
        room = self.one("SELECT r.*,s.title,s.ends,s.cancelled,s.vip FROM rooms r JOIN screenings s ON s.id=r.screening WHERE r.id=?", (rid,))
        if not room:
            raise Problem("Xona topilmadi", 404)
        room["members"] = self.rows("SELECT user_id,name FROM members WHERE room=? AND seen>? ORDER BY user_id LIMIT 20", (rid,self.clock()-75))
        room["messages"] = self.one("SELECT COUNT(*) n FROM messages WHERE scope=? AND deleted=0",(rid,))["n"]
        return room

    def dashboard(self, user):
        admin(user)
        now=self.clock()
        return {"screenings": self.rows("""SELECT s.id,s.title,s.starts,s.ends,s.vip,s.cancelled,
                    COALESCE(v.viewers,0) viewers FROM screenings s LEFT JOIN
                    (SELECT screening,COUNT(*) viewers FROM screening_visitors GROUP BY screening) v ON v.screening=s.id
                    WHERE s.personal=0 ORDER BY s.starts DESC LIMIT 100"""),
                "rooms": self.rows("""SELECT r.id,r.private,r.owner,s.title,s.vip,s.ends,
                    COUNT(m.user_id) members FROM rooms r JOIN screenings s ON s.id=r.screening
                    LEFT JOIN members m ON m.room=r.id AND m.seen>?
                    WHERE s.cancelled=0 AND s.starts<=? AND s.ends>?
                    GROUP BY r.id ORDER BY members DESC,r.created DESC LIMIT 100""", (now-75,now,now)),
                "room_count": self.one("SELECT COUNT(*) n FROM rooms r JOIN screenings s ON s.id=r.screening WHERE s.cancelled=0 AND s.starts<=? AND s.ends>?",(now,now))["n"],
                "reports": self.rows("SELECT r.*,m.text,m.name,m.user_id author_id FROM reports r JOIN messages m ON m.id=r.message_id WHERE m.deleted=0 ORDER BY r.created DESC LIMIT 50"),
                "online": self.one("SELECT COUNT(*) n FROM members m JOIN rooms r ON r.id=m.room JOIN screenings s ON s.id=r.screening WHERE m.seen>? AND s.cancelled=0 AND s.starts<=? AND s.ends>?", (now-75,now,now))["n"],
                "messages": self.one("SELECT COUNT(*) n FROM messages WHERE deleted=0")["n"],
                "audit": self.rows("SELECT * FROM audit ORDER BY id DESC LIMIT 30")}


    def my_cabinets(self, user):
        self.allowed(user)
        return self.rows("SELECT r.id,r.name title,s.title movie_title,r.locked FROM rooms r JOIN screenings s ON s.id=r.screening WHERE r.owner=? AND s.personal=1 AND s.cancelled=0 ORDER BY r.created DESC LIMIT 10", (user['id'],))

    def create_cabinet(self, user, name, source, aid, duration):
        self.allowed(user, writing=True)
        name = str(name).strip()
        if not 1 <= len(name) <= 60:
            raise Problem('Kabinet nomi 1–60 belgi bo‘lsin')
        if source.get('vip') and user.get('vip_until',0) <= self.clock():
            raise Problem('Bu kino uchun shaxsiy VIP obuna kerak',403)
        if len(self.my_cabinets(user)) >= 5:
            raise Problem('5 tagacha kabinet yaratish mumkin. Avval eskisini yoping.',409)
        sid,rid=secrets.token_urlsafe(18),secrets.token_urlsafe(18)
        now=self.clock()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute("INSERT INTO screenings(id,title,description,movie_code,asset_id,starts,ends,duration,vip,created_by,personal) VALUES(?,?,?,?,?,?,?,?,?,?,1)",
                (sid,source['title'],source.get('description',''),source['code'],aid,now,253402300799,duration,int(bool(source.get('vip'))),user['id']))
            self.db.execute('INSERT INTO rooms(id,screening,private,owner,updated,created,locked,name) VALUES(?,?,1,?,?,?,1,?)',(rid,sid,user['id'],now,now,name))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        return {'id':rid}

    def close_cabinet(self, user, rid):
        self.allowed(user, writing=True)
        row=self.one('SELECT r.owner,r.screening,s.personal FROM rooms r JOIN screenings s ON s.id=r.screening WHERE r.id=?',(rid,))
        if not row or not row['personal'] or row['owner']!=user['id']:
            raise Problem('Faqat o‘zingizning kabinetingizni yopishingiz mumkin',403)
        self.db.execute('UPDATE screenings SET cancelled=1 WHERE id=?',(row['screening'],))
        return {'ok':True}

    def profile_identity(self, user):
        self.allowed(user)
        self.db.execute("INSERT INTO profiles VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET name=excluded.name,photo=excluded.photo",
                        (user["id"], str(user["name"])[:100], user.get("photo", ""), secrets.token_urlsafe(9)))
        return self.one("SELECT friend_code FROM profiles WHERE user_id=?", (user["id"],))

    def social(self, user):
        profile = self.profile_identity(user)
        uid = user["id"]
        profile["friends"] = self.rows("""SELECT p.user_id,p.name,p.photo FROM friendships f JOIN profiles p
            ON p.user_id=CASE WHEN f.sender=? THEN f.recipient ELSE f.sender END
            WHERE (f.sender=? OR f.recipient=?) AND f.accepted=1 ORDER BY p.name LIMIT 200""", (uid,uid,uid))
        profile["requests"] = self.rows("SELECT p.user_id,p.name FROM friendships f JOIN profiles p ON p.user_id=f.sender WHERE f.recipient=? AND f.accepted=0 LIMIT 50", (uid,))
        profile["sent"] = self.rows("SELECT p.user_id,p.name FROM friendships f JOIN profiles p ON p.user_id=f.recipient WHERE f.sender=? AND f.accepted=0 LIMIT 50", (uid,))
        profile["favorites"] = self.rows("SELECT s.id,s.title,s.ends,s.starts,s.cancelled FROM favorites f JOIN screenings s ON s.id=f.screening WHERE f.user_id=? ORDER BY f.created DESC LIMIT 100", (uid,))
        profile["history"] = self.rows("SELECT s.id,s.title,s.ends,s.starts,s.cancelled,v.last_seen,(SELECT r.id FROM rooms r WHERE r.screening=s.id AND s.personal=1 LIMIT 1) room FROM screening_visitors v JOIN screenings s ON s.id=v.screening WHERE v.user_id=? ORDER BY v.last_seen DESC LIMIT 50", (uid,))
        profile["rooms"] = self.my_cabinets(user)
        profile["invitations"] = self.rows("""SELECT i.room,p.name,COALESCE(NULLIF(r.name,''),s.title) title FROM room_invitations i JOIN rooms r ON r.id=i.room
            JOIN screenings s ON s.id=r.screening JOIN profiles p ON p.user_id=i.sender
            WHERE i.user_id=? AND s.cancelled=0 AND s.ends>? ORDER BY i.created DESC LIMIT 30""", (uid,self.clock()))
        return profile

    def invite_friend(self, user, data):
        self.allowed(user, writing=True)
        rid = str(data.get("room", ""))
        self.room(user,rid)
        uid = integer(data.get("user_id"),1,2**63-1)
        if not self.one("SELECT 1 FROM friendships WHERE accepted=1 AND ((sender=? AND recipient=?) OR (sender=? AND recipient=?))", (uid,user["id"],user["id"],uid)):
            raise Problem("Avval do‘stlik so‘rovi qabul qilinishi kerak",403)
        self.db.execute("INSERT OR IGNORE INTO room_invitations VALUES(?,?,?,?)",(rid,uid,user["id"],self.clock()))
        return {"ok":True}

    def friend_action(self, user, data):
        self.profile_identity(user)
        self.allowed(user, writing=True)
        uid = user["id"]
        action = data.get("action")
        if action == "request":
            target = self.one("SELECT user_id FROM profiles WHERE friend_code=?", (str(data.get("code", ""))[:100],))
            if not target or target["user_id"] == uid:
                raise Problem("Do‘st kodi topilmadi yoki o‘zingizniki")
            other = target["user_id"]
            for person in (uid,other):
                if self.one("SELECT COUNT(*) n FROM friendships WHERE sender=? OR recipient=?", (person,person))["n"] >= 200:
                    raise Problem("Do‘stlar ro‘yxati limiti 200 ta", 409)
            if not self.one("SELECT 1 FROM friendships WHERE (sender=? AND recipient=?) OR (sender=? AND recipient=?)", (uid,other,other,uid)):
                self.db.execute("INSERT INTO friendships VALUES(?,?,0,?)", (uid,other,self.clock()))
        else:
            other = integer(data.get("user_id"), 1, 2**63-1)
            if action == "accept":
                self.db.execute("UPDATE friendships SET accepted=1 WHERE sender=? AND recipient=?", (other,uid))
            elif action == "remove":
                self.db.execute("DELETE FROM friendships WHERE (sender=? AND recipient=?) OR (sender=? AND recipient=?)", (uid,other,other,uid))
            else:
                raise Problem("Amal noto‘g‘ri")
        return {"ok": True}

    def favorite(self, user, data):
        self.allowed(user)
        sid = str(data.get("screening", ""))
        if not self.one("SELECT id FROM screenings WHERE id=?", (sid,)):
            raise Problem("Kino topilmadi", 404)
        if data.get("saved"):
            if self.one("SELECT COUNT(*) n FROM favorites WHERE user_id=?", (user["id"],))["n"] >= 100:
                raise Problem("100 tagacha kino saqlash mumkin")
            self.db.execute("INSERT OR IGNORE INTO favorites VALUES(?,?,?)", (user["id"],sid,self.clock()))
        else:
            self.db.execute("DELETE FROM favorites WHERE user_id=? AND screening=?", (user["id"],sid))
        return {"ok": True}

    def request_access(self, user, rid):
        self.allowed(user, writing=True)
        room = self.one("SELECT * FROM rooms WHERE id=?", (rid,))
        if not room:
            raise Problem("Xona topilmadi", 404)
        self.screening(room["screening"], user)
        if not room["locked"] or room["owner"] == user["id"]:
            return {"status": "approved"}
        previous = self.one("SELECT status FROM room_access WHERE room=? AND user_id=?", (rid,user["id"]))
        if previous:
            return previous
        if self.one("SELECT COUNT(*) n FROM room_access WHERE room=? AND status='pending'", (rid,))["n"] >= 30:
            raise Problem("Kabinetning so‘rovlar navbati to‘lgan", 429)
        self.db.execute("INSERT INTO room_access VALUES(?,?,?,'pending',?)", (rid,user["id"],str(user["name"])[:100],self.clock()))
        return {"status": "pending"}

    def access_action(self, user, data):
        rid = str(data.get("room", ""))
        room = self.room(user, rid)
        if not room["private"] or room["owner"] != user["id"]:
            raise Problem("Faqat kabinet egasi boshqaradi", 403)
        action = data.get("action")
        if action == "lock":
            locked = bool(data.get("locked"))
            if locked:
                self.db.execute("INSERT INTO room_access SELECT room,user_id,name,'approved',? FROM members WHERE room=? ON CONFLICT(room,user_id) DO UPDATE SET status='approved'", (self.clock(),rid))
            self.db.execute("UPDATE rooms SET locked=? WHERE id=?", (int(locked),rid))
        elif action in ("approve", "reject"):
            uid = integer(data.get("user_id"), 1, 2**63-1)
            self.db.execute("UPDATE room_access SET status=? WHERE room=? AND user_id=?", ("approved" if action=="approve" else "rejected",rid,uid))
        else:
            raise Problem("Amal noto‘g‘ri")
        return {"ok": True}

    def poll(self, user, rid):
        room = self.room(user, rid)
        # One next-movie ballot per room; only published, available screenings are options.
        choices = self.rows("""SELECT s.id,s.title,s.vip,COUNT(v.user_id) votes FROM screenings s
            LEFT JOIN movie_votes v ON v.screening=s.id AND v.room=?
            WHERE s.personal=0 AND s.cancelled=0 AND s.ends>? AND s.id!=? GROUP BY s.id
            ORDER BY votes DESC,s.starts LIMIT 30""", (rid,self.clock(),room["screening_id"]))
        mine = self.one("SELECT screening FROM movie_votes WHERE room=? AND user_id=?", (rid,user["id"]))
        return {"choices": choices, "selected": mine["screening"] if mine else None}

    def vote(self, user, data):
        rid = str(data.get("room", ""))
        self.allowed(user, writing=True)
        choices = self.poll(user, rid)["choices"]
        sid = str(data.get("screening", ""))
        if sid not in {s["id"] for s in choices}:
            raise Problem("Ovoz berish uchun ro‘yxatdagi kinoni tanlang")
        self.db.execute("INSERT INTO movie_votes VALUES(?,?,?) ON CONFLICT(room,user_id) DO UPDATE SET screening=excluded.screening", (rid,user["id"],sid))
        return self.poll(user, rid)

    def diagnostic(self, user, data):
        self.allowed(user)
        rid = str(data.get("room", ""))
        self.room(user, rid)
        kind = data.get("kind")
        if kind not in {"video_network", "video_decode", "video_format", "playback_unavailable", "video_unknown"}:
            raise Problem("Xato turi noto‘g‘ri")
        if not self.one("SELECT id FROM diagnostics WHERE user_id=? AND room=? AND kind=? AND created>?", (user["id"],rid,kind,self.clock()-300)):
            self.db.execute("INSERT INTO diagnostics(user_id,room,kind,created) VALUES(?,?,?,?)", (user["id"],rid,kind,self.clock()))
            self.db.execute("DELETE FROM diagnostics WHERE id NOT IN (SELECT id FROM diagnostics ORDER BY id DESC LIMIT 500)")
        return {"ok": True}

    def diagnostic_list(self, user):
        admin(user)
        return self.rows("SELECT d.id,d.kind,d.created,d.user_id,s.title FROM diagnostics d JOIN rooms r ON r.id=d.room JOIN screenings s ON s.id=r.screening ORDER BY d.id DESC LIMIT 50")

    def call(self, method, *args, **kwargs):
        with self.lock:
            return getattr(self, method)(*args, **kwargs)
