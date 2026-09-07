# Kino Club — Railway

Bu paket faqat Mini App xizmatidir. Bot tokeni va ishlab turgan baza nusxalari kiritilmagan.

1. GitHubda private kino-miniapp repository yarating.
2. ZIPni ochib, ichidagi fayllar va miniapp papkasini repository ildiziga yuklang. ZIPning o‘zini yuklamang.
3. Railway loyihasida GitHub repositorydan yangi xizmat qo‘shing.
4. Shu xizmatga alohida Volume ulang: mount path /data. Postgres diskini ulashmang.
5. Variables: MINIAPP_DB=/data/miniapp.sqlite va RAILWAY_RUN_UID=0.
6. Networking orqali domain yarating. PUBLIC_ORIGIN=https://olingan-manzil qilib kiriting.
7. BOT_USERNAME, BOT_BRIDGE_URL, MINIAPP_BRIDGE_SECRET va S3 media sozlamalari ulanishi kerak.

/health faqat xizmat ishga tushganini bildiradi. Bot bridge va media sozlanmaguncha foydalanuvchi funksiyalari to‘liq ishlamaydi. Parol/tokenlarni GitHubga yuklamang, Railway Variables ichida saqlang.

Railway hujjatlari: https://docs.railway.com/volumes
