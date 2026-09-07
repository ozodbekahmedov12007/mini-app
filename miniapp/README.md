# Kino Club Mini App

Mini App must run on a **separate host**. The existing bot server only verifies
Telegram identities, current admin membership and VIP expiry, and supplies admin
catalog searches. Video, voice messages, round videos and WebSocket traffic do
not pass through the bot host.

## Implemented

- Telegram signed `initData` verification on the bot side, 12-hour maximum age,
  30-second identity cache on the Mini App service; role changes fail closed when
  the bridge is unavailable after the cache expires.
- Admin-only scheduling, importing movie title/code/VIP flag from the bot,
  direct multipart upload to a private S3-compatible bucket (32 MiB parts).
- Public screening rooms allocated atomically with a 20-member maximum. A 21st
  visitor gets the next room. Invite-only private rooms never spill into another
  room. Private links are bearer invitations; membership/VIP is still checked.
- Public scheduled playback, private host play/pause/seek, automatic host transfer,
  75-second presence leases, reconnection, one active room per Telegram user.
- Every viewer needs their own active VIP subscription for VIP screenings,
  including admins. Admin status grants management, not a shared VIP entitlement.
- Screening end/cancellation closes access across all rooms and removes the
  screening from the public list. Global text chat persists independently.
- Per-room and global chat, 50-message cursor pagination, reply references,
  avatars, report/delete/mute/ban, rate limits, admin audit records.
- Browser MediaRecorder voice and circular video, 60-second recording cap,
  upload fallback, 8 MiB voice/20 MiB video limits and 7-day media retention.
  Uploaded recordings are shown in a circular player, not Telegram-native video notes.

## External prerequisites — not yet provisioned

1. A separate persistent host/container platform (one instance, attached disk)
   and an HTTPS domain pointing to it. An ephemeral/serverless filesystem is
   **not** suitable for the SQLite database.
2. Private S3-compatible bucket with multipart API and byte-range GET support.
   AWS-style SigV4 `Content-Length` signing must work with the chosen provider.
   Dedicated bucket-scoped credentials; public access disabled. Provider storage
   and traffic are paid according to its plan; this repository provisions none.
3. A secure private route or HTTPS reverse proxy to the optional bot bridge.
   Never publish the bot database or the bridge without its shared secret.
4. Configure the bot's Main Mini App URL in BotFather, so
   `https://t.me/BOT_USERNAME?startapp=room_ROOM_ID` opens the correct Mini App.

## Deployment

On the **separate** host, copy the repository without `.env`, backups, production
database dumps, local test screenshots or Python caches. Then:

```sh
cp deploy/miniapp/.env.example deploy/miniapp/.env
# Fill actual domain, bridge address, matching secret and bucket credentials.
docker compose -f deploy/miniapp/compose.yml up -d --build
```

Use `deploy/miniapp/Caddyfile` after replacing the example domain. TLS terminates
there; only `127.0.0.1:8080` is published by Compose. Don't enable access logging
of query strings: signed media URLs and one-use socket tickets are credentials.
Static files can later move to a CDN; keep `/api` and `/ws` on the app origin.

On the existing bot, set these variables only after the network route is ready:

```dotenv
MINIAPP_BRIDGE_PORT=8091
MINIAPP_BRIDGE_HOST=0.0.0.0
MINIAPP_BRIDGE_SECRET=<random secret of at least 32 characters>
```

The bridge is **off by default** (`MINIAPP_BRIDGE_PORT=0`). Because the bot runs in
Docker, a host reverse proxy needs a loopback-only port mapping
`127.0.0.1:8091:8091` on the bot service. Use a private VPN or TLS on the route
from the Mini App host. Restrict ingress to the Mini App service where possible.
The Mini App host never needs the bot token or production DB credentials.

Apply the bucket CORS and lifecycle examples after replacing the origin. JSON
format may need adjustment for the chosen provider. Lifecycle aborts abandoned
multipart uploads after one day and expires `chat/` objects after seven days.
The service's hourly cleanup also removes expired media and unused movie copies
older than a day, preserving assets scheduled for future screenings. This never
deletes the original Telegram movie. Existing text chat is retained.

Back up the Mini App database with SQLite's backup API (not a raw copy of an
active WAL file). Keep the persistent volume across upgrades. Stop the service
before restoring a backup. Run only one replica; scale-out requires a shared DB
and pub/sub layer, which this initial version deliberately does not pretend to have.

## Video behavior and practical limits

The bot's Telegram `file_id` is not a browser playback URL. Importing a catalog
entry copies metadata only; the admin supplies the video file separately. MP4
with H.264/AAC and a front-loaded `moov` atom is the supported movie input. This
version serves the original file via signed object-store URLs; it does not
transcode or provide adaptive HLS quality. Transcode/fast-start on an external
worker or administrator machine before uploading, never on the small bot host.

URLs expire after at most 120 seconds (capped to the scheduled end), and every
new link requires room membership and VIP checks. An already-started download
may continue after a URL expires. Browser timers stop normal playback at the
scheduled end; web media cannot be made impossible to save or screen-record.
Playback synchronization is best effort; network/browser buffering still exists.
Private rooms close at the screening's scheduled end even if their host paused.

Microphone/camera recording requires HTTPS, user permission, and a supported
Telegram WebView. Real iOS/Android permissions, selected S3 provider, actual
large video transfers and live playback require deployment acceptance checks.
The synthetic 3 GiB test verifies multipart metadata, not a real 3 GiB transfer.

## Local verification

```sh
python -m pip install -r miniapp/requirements.txt
python -m unittest discover -s miniapp/tests -v
node --check miniapp/static/app.js
python tests/sanity_check.py
```

Optional local browser regression (uses in-memory fixtures, no real Telegram,
no real users, no production database):

```sh
python -m pip install playwright
playwright install chromium
python -m miniapp.tests.browser_check
```

Unauthenticated local preview:

```sh
MINIAPP_DB=/tmp/kino-club-preview.sqlite PUBLIC_ORIGIN=http://127.0.0.1:8080 python -m miniapp.server
```

No demo/admin bypass is included in the production service. The browser test
injects a fake bridge only into its own temporary in-memory test application.
